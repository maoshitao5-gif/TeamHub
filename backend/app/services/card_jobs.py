"""逐资料授权的后台理解任务；失败不重放，换版才按已开启策略更新。"""
import asyncio
import json
import threading

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, text, update

from backend.app import database
from backend.app.api.ai import _paid_call
from backend.app.card_job_models import CardRefreshPolicy, CardRequest, DocumentCardJob
from backend.app.config import settings
from backend.app.core import ai_settings
from backend.app.core.ai_client import AIError
from backend.app.core.hermes_agent import run_agent
from backend.app.core.logger import get_logger
from backend.app.models import Document, Version, generate_uuid, utc_now
from backend.app.services import preorganize
from backend.app.services.structured_diagnostics import (
    StructuredDiagnostics, log_diagnostics, sanitize_diagnostics,
)

LIMITS = {'model_requests': 20, 'tool_calls': 60, 'output_tokens': 24000, 'time_seconds': 300}
logger = get_logger('services.card_jobs')
_process_lock = threading.Lock()
_worker = None
_running = None
_recovered_binding = None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def get_config():
    return {key: value for key, value in preorganize.get_config().items() if key != 'policy'}


def _begin(db, token=None):
    if token is not None:
        preorganize._check_library(token)
    db.execute(text('BEGIN IMMEDIATE'))


def _document(db, document_id):
    doc = db.get(Document, document_id)
    if doc is None or doc.status not in {'pending', 'organized'}:
        raise HTTPException(404, '资料不存在或暂不可用')
    return doc


def _current(db, record, doc):
    if not record or not doc or doc.status not in {'pending', 'organized'}:
        return False
    try:
        return json.loads(record.scope_json) == preorganize._freeze(db, doc)
    except HTTPException:
        return False


def _policy(policy, public, revision):
    enabled = bool(policy and policy.enabled)
    reason = None
    if enabled:
        if not public['configured']:
            reason = '请先配置 AI 服务'
        elif policy.service_revision != public['service_revision'] or policy.connection_revision != revision:
            reason = 'AI 服务配置已变化，自动更新已暂停；可重新开启'
    return {'enabled': enabled, 'active': enabled and reason is None, 'paused_reason': reason}


def get_job_status(document_id):
    public, _, _, revision = preorganize._connection()
    with database.SessionLocal() as db:
        _document(db, document_id)
        record = db.get(DocumentCardJob, document_id)
        return {'job': {'state': record.state if record else 'waiting',
                        'error': record.error if record else None,
                        'id': record.id if record else None},
                'policy': _policy(db.get(CardRefreshPolicy, document_id), public, revision)}


def _set_policy(db, document_id, enabled, public, revision):
    policy = db.get(CardRefreshPolicy, document_id)
    if policy is None:
        policy = CardRefreshPolicy(document_id=document_id)
        db.add(policy)
    policy.enabled, policy.service_revision = enabled, public['service_revision']
    policy.connection_revision, policy.updated_at = revision or '', utc_now()


def _queue(db, doc, public, revision, automatic=False):
    from backend.app.services import document_cards
    scope = preorganize._freeze(db, doc)
    record = db.get(DocumentCardJob, doc.id)
    if (record and record.state in {'queued', 'running'} and _current(db, record, doc)
            and record.service_revision == public['service_revision']
            and record.connection_revision == revision):
        if not automatic:
            record.automatic = False
        return record
    if record is None:
        record = DocumentCardJob(document_id=doc.id, generation=0)
        db.add(record)
    status = document_cards.status(db, doc.id)
    card = status['card']
    if card is None and status.get('previous_card', {}).get('version_id') == scope['version_id']:
        card = status['previous_card']
    record.id, record.generation = generate_uuid(), record.generation + 1
    record.state, record.scope_json = 'queued', _json(scope)
    record.service_revision, record.connection_revision = public['service_revision'], revision
    record.expected_card_revision = card['revision'] if card else 0
    record.automatic, record.error, record.updated_at = automatic, None, utc_now()
    return record


def start(document_id, library_token, service_revision, consent, request_id, auto_update=False):
    preorganize._check_library(library_token)
    public, _, _, revision = preorganize._authorized(service_revision, consent)
    receipt = _json({'document_id': document_id, 'service_revision': service_revision,
                     'auto_update': auto_update})
    with database.SessionLocal() as db:
        _begin(db, library_token)
        doc = _document(db, document_id)
        previous = db.get(CardRequest, request_id)
        if previous and previous.input_json != receipt:
            raise HTTPException(409, '请求编号已用于其他任务，请刷新后重试')
        if previous is None:
            _queue(db, doc, public, revision)
            _set_policy(db, doc.id, auto_update, public, revision)
            db.add(CardRequest(id=request_id, input_json=receipt))
        db.commit()
    return get_job_status(document_id)


def _cancel_running(identity):
    entry = _running
    if entry and (entry['binding'], entry['document_id'], entry['id'], entry['generation']) == identity:
        task = entry['task']
        loop = task.get_loop()
        if not loop.is_closed() and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # 循环已在关闭，旧任务的 generation 仍已失效。


def _cancel_record(record):
    identity = (preorganize._binding(), record.document_id, record.id, record.generation)
    record.generation += 1
    record.state, record.error = 'waiting', '已取消，可再次生成资料卡'
    record.updated_at = utc_now()
    return identity


def cancel(document_id, library_token):
    identity = None
    with database.SessionLocal() as db:
        _begin(db, library_token)
        _document(db, document_id)
        record = db.get(DocumentCardJob, document_id)
        if record:
            identity = _cancel_record(record)
        db.commit()
    _cancel_running(identity)
    return get_job_status(document_id)


def set_policy(document_id, library_token, enabled, service_revision=None, consent=False):
    if enabled:
        public, _, _, revision = preorganize._authorized(service_revision, consent)
    else:
        public, _, _, revision = preorganize._connection()
    identity = None
    with database.SessionLocal() as db:
        _begin(db, library_token)
        doc = _document(db, document_id)
        _set_policy(db, document_id, enabled, public, revision)
        record = db.get(DocumentCardJob, document_id)
        if not enabled and record and record.automatic and record.state in {'queued', 'running'}:
            identity = _cancel_record(record)
        db.commit()
    _cancel_running(identity)
    return get_job_status(document_id)


def _discover():
    from backend.app.services import document_cards
    public, _, _, revision = preorganize._connection()
    with database.SessionLocal() as db:
        _begin(db)
        rows = (db.query(Document).join(CardRefreshPolicy, CardRefreshPolicy.document_id == Document.id)
                .join(Version, and_(Version.id == Document.current_version_id, Version.document_id == Document.id))
                .outerjoin(DocumentCardJob, DocumentCardJob.document_id == Document.id)
                .filter(Document.status.in_(('pending', 'organized')),
                        CardRefreshPolicy.enabled.is_(True),
                        CardRefreshPolicy.service_revision == public['service_revision'],
                        CardRefreshPolicy.connection_revision == revision,
                        or_(DocumentCardJob.document_id.is_(None),
                            func.json_extract(DocumentCardJob.scope_json, '$.version_id').is_distinct_from(Version.id),
                            func.json_extract(DocumentCardJob.scope_json, '$.content_hash').is_distinct_from(Version.sha256_hash)))
                .order_by(Document.id).limit(100).all()) if public['configured'] else []
        for doc in rows:
            current = document_cards.status(db, doc.id)
            record = _queue(db, doc, public, revision, automatic=True)
            if current['state'] in {'ready', 'partial'} and current['card']:
                # 恢复已有卡片的历史版本，只登记复用，不产生外部请求。
                record.state = 'ready'
        db.commit()


def _recover():
    global _recovered_binding
    binding = preorganize._binding()
    if binding == _recovered_binding:
        return
    with database.SessionLocal() as db:
        db.execute(update(DocumentCardJob).where(DocumentCardJob.state == 'running').values(
            state='failed', error='上次生成在退出或切库时中断，请主动重试；此前请求可能已计费',
            updated_at=utc_now()))
        db.commit()
    _recovered_binding = binding


def _claim():
    from backend.app.services.card_context import understanding_context
    public, config, key, revision = preorganize._connection()
    with database.SessionLocal() as db:
        _begin(db)
        record = db.query(DocumentCardJob).filter_by(state='queued').order_by(DocumentCardJob.updated_at).first()
        if record is None:
            return None
        doc = db.get(Document, record.document_id)
        error = None
        if not _current(db, record, doc):
            error = '资料或整理信息已变化，请重新生成资料卡'
        elif not config or record.service_revision != public['service_revision'] or record.connection_revision != revision:
            error = 'AI 服务配置已变化，请重新生成资料卡'
        if error:
            record.state, record.error, record.updated_at = 'failed', error, utc_now()
            db.commit()
            return {'skipped': True}
        record.state, record.updated_at = 'running', utc_now()
        job = {'binding': preorganize._binding(), 'engine': db.get_bind(), 'document_id': doc.id,
               'id': record.id, 'generation': record.generation, 'scope': json.loads(record.scope_json),
               'config': {**config, 'thinking_mode': 'disabled'}, 'key': key, 'revision': revision,
               'expected_card_revision': record.expected_card_revision,
               'understanding': understanding_context(db, doc.id)}
        db.commit()
        return job


def _guard(job):
    if preorganize._binding() != job['binding']:
        raise AIError(409, 'CARD_LIBRARY_CHANGED', '资料库已切换，本次资料卡生成已停止')
    if ai_settings.get_connection()[2] != job['revision']:
        raise AIError(409, 'CARD_SERVICE_CHANGED', 'AI 服务配置已变化，请重新生成')
    with database.SessionLocal() as db:
        record = db.get(DocumentCardJob, job['document_id'])
        if (record is None or record.id != job['id'] or record.generation != job['generation']
                or record.state != 'running' or preorganize._binding() != job['binding']
                or not _current(db, record, db.get(Document, job['document_id']))):
            raise AIError(409, 'CARD_CHANGED', '资料或任务已变化，本次生成已停止')


def _finish(job, result=None, error=None, diagnostics=None):
    from backend.app.services import document_cards
    diagnostics = sanitize_diagnostics(diagnostics or (result or {}).get('diagnostics'), 'card')
    if result is not None and not isinstance(result.get('card'), dict):
        result, error = None, error or 'AI 未能形成有效资料卡，请重试'
    if preorganize._binding() != job['binding']:
        return
    with database.SessionLocal() as db:
        _begin(db)
        record = db.get(DocumentCardJob, job['document_id'])
        if (record is None or record.id != job['id'] or record.generation != job['generation']
                or record.state != 'running' or preorganize._binding() != job['binding']):
            return
        if not _current(db, record, db.get(Document, job['document_id'])):
            result, error = None, '资料已变化，请重新生成资料卡'
            diagnostics = {}
        with ai_settings._lock:
            if result is not None or diagnostics:
                try:
                    revision = ai_settings.get_connection()[2]
                except ai_settings.AISettingsError:
                    revision = None
                if revision != job['revision']:
                    result, error = None, 'AI 服务配置已变化，请重新生成资料卡'
                    diagnostics = {}
            if result is not None:
                saved = document_cards.save_card(db, job['document_id'], job['scope']['version_id'],
                    job['scope']['content_hash'], result['card'], result['sources'], result['coverage'],
                    job['config']['model'], expected_revision=job['expected_card_revision'], commit=False)
                if saved['card']['revision'] == job['expected_card_revision']:
                    error = '本次阅读范围少于已有成果，已保留原资料卡；可查看缺口后再决定是否重新生成'
            record.state = 'ready' if result is not None else 'failed'
            record.error, record.updated_at = error, utc_now()
            db.commit()
            log_diagnostics(logger, 'card', record.state, diagnostics)


def _error(exc):
    from backend.app.core.partner_budget import PartnerPaused
    if isinstance(exc, PartnerPaused):
        return '本次阅读达到处理上限，尚未形成有效资料卡；已有资料卡保持不变，可稍后重试'
    if isinstance(exc, AIError):
        return str(exc)
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        return exc.detail
    return '资料卡生成未完成，可稍后重试；本次不会自动重复调用'


async def process_one():
    global _running
    if not settings.library_path or not _process_lock.acquire(blocking=False):
        return False
    job, lease = None, False
    diagnostics = StructuredDiagnostics('card')
    try:
        await asyncio.to_thread(_recover)
        await asyncio.to_thread(_discover)
        from backend.app.services.partner_runtime import has_pending_interactive_tasks
        if has_pending_interactive_tasks() or not _paid_call.acquire(blocking=False):
            return False
        lease = True
        job = await asyncio.to_thread(_claim)
        if not job:
            return False
        if job.get('skipped'):
            return True
        _running = {'task': asyncio.current_task(), **{key: job[key] for key in (
            'binding', 'document_id', 'id', 'generation')}}
        result = await run_agent(message='请为这份资料建立或补全有原文依据的资料卡，提交 submit_document_card。',
            history=[], scope={job['document_id']: job['scope']}, config=job['config'], key=job['key'],
            engine=job['engine'], root=job['binding'][0], guard=lambda: _guard(job), emit=diagnostics,
            limits=dict(LIMITS), scope_mode='selected', purpose='card',
            purpose_context={'document_id': job['document_id'], **job['understanding']})
        diagnostics.accept_output(result)
        await asyncio.to_thread(_guard, job)
        await asyncio.to_thread(_finish, job, result, diagnostics=diagnostics.value)
        return True
    except asyncio.CancelledError:
        if job and not job.get('skipped'):
            await asyncio.to_thread(_finish, job, error='资料卡生成已中断，可主动重试；此前请求可能已计费',
                                    diagnostics=diagnostics.value)
        raise
    except Exception as exc:
        if job and not job.get('skipped'):
            await asyncio.to_thread(_finish, job, error=_error(exc), diagnostics=diagnostics.value)
            return True
        raise
    finally:
        _running = None
        if lease:
            _paid_call.release()
        _process_lock.release()


async def _worker_loop():
    while True:
        try:
            worked = await asyncio.create_task(process_one())
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            worked = False
        except Exception:
            logger.exception('资料卡队列检查失败；运行中的付费请求不会自动重放')
            worked = False
        await asyncio.sleep(.15 if worked else 2)


def start_worker():
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_worker_loop(), name='teamhub-document-cards')


async def stop_worker():
    global _worker
    if _worker and not _worker.done():
        _worker.cancel()
        await asyncio.gather(_worker, return_exceptions=True)
    _worker = None
