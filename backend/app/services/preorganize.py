"""待整理 AI 方案：显式授权、短事务队列及用户确认后的可撤销采用。"""
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import threading
import unicodedata

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal_column, or_, text, update

from backend.app import database
from backend.app.api.ai import _paid_call
from backend.app.config import settings
from backend.app.core import ai_settings
from backend.app.core.ai_client import AIError
from backend.app.core.changelog import record_change
from backend.app.core.file_utils import calculate_sha256
from backend.app.core.hermes_agent import run_agent
from backend.app.core.logger import get_logger
from backend.app.core.storage_paths import (
    classification_directory, classification_folder_roots, document_path, working_location,
)
from backend.app.models import Document, Tag, Version, generate_uuid, utc_now
from backend.app.preorganize_models import (
    PreorganizeOperation, PreorganizePolicy, PreorganizeProposal, PreorganizeRequest,
)
from backend.app.services.document_responses import build_document_responses
from backend.app.services.indexing import _CURRENT_METADATA_SQL, _metadata_json, metadata_fingerprint
from backend.app.services.preorganize_storage import get_or_create_tags, organize_working, working_changes
from backend.app.services.structured_diagnostics import (
    StructuredDiagnostics, log_diagnostics, sanitize_diagnostics,
)

logger = get_logger('services.preorganize')
LIMITS = {'model_requests': 12, 'tool_calls': 32, 'output_tokens': 16000, 'time_seconds': 180}
STATES = ('waiting', 'processing', 'ready', 'failed')
_epoch = secrets.token_hex(16)
_process_lock = threading.Lock()
_worker = None
_running = None


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _binding():
    root = str(Path(settings.library_path).resolve()) if settings.library_path else ''
    return root, str(database.engine.url), id(database.engine)


def _library_token():
    return hashlib.sha256(_json([_epoch, *_binding()]).encode()).hexdigest()


def _check_library(token):
    if not settings.library_path:
        raise HTTPException(400, '请先打开个人资料库')
    if not secrets.compare_digest(str(token).encode(), _library_token().encode()):
        raise HTTPException(409, '资料库已切换，请刷新待整理后重试')


def _connection():
    from backend.app.services.partner_runtime import chat_config
    public = chat_config()
    if not public['configured']:
        return public, None, None, None
    config, key, revision = ai_settings.get_connection()
    return public, config, key, revision


def _policy_payload(policy, public, revision):
    enabled = bool(policy and policy.enabled)
    reason = None
    if enabled:
        if not public['configured']:
            reason = '请先配置 AI 服务'
        elif (policy.service_revision != public['service_revision']
              or policy.connection_revision != revision):
            reason = 'AI 服务配置已变化，请重新开启自动预整理'
    return {'enabled': enabled, 'active': enabled and reason is None, 'paused_reason': reason}


def get_config():
    public, _, _, revision = _connection()
    with database.SessionLocal() as db:
        policy = db.get(PreorganizePolicy, 1)
        return {'library_token': _library_token(), 'service': public['service'],
                'service_revision': public['service_revision'], 'configured': public['configured'],
                'policy': _policy_payload(policy, public, revision)}


def _authorized(service_revision, consent):
    public, config, key, revision = _connection()
    if not consent:
        raise HTTPException(400, '请先允许所选范围的资料发送给当前 AI 服务')
    if service_revision != public['service_revision']:
        raise HTTPException(409, 'AI 服务或模型已变化，请刷新后重新启动预整理')
    if not config:
        raise HTTPException(400, '请先配置 AI 服务密钥')
    return public, config, key, revision


def _begin(db, token=None):
    # 先取得库会话防切库，再抢写锁并重读；避免旧页面确认覆盖新整理结果。
    if token is not None:
        _check_library(token)
    db.execute(text('BEGIN IMMEDIATE'))


def _document(db, document_id):
    doc = db.get(Document, document_id)
    if not doc or doc.status != 'pending':
        raise HTTPException(409, '资料已不在待整理，请刷新后重试')
    return doc


def _freeze(db, doc):
    version = db.get(Version, doc.current_version_id) if doc.current_version_id else None
    if not version or version.document_id != doc.id or not version.sha256_hash:
        raise HTTPException(409, '资料尚无可读取的已保存版本，请先保存版本')
    result = {'name': doc.name, 'version_id': version.id, 'version_number': version.version_number,
            'content_hash': version.sha256_hash, 'metadata_hash': metadata_fingerprint(doc),
            'is_folder': doc.is_folder}
    from backend.app.services.object_skills import object_kind
    if not doc.is_folder and object_kind(version.original_filename) == 'archive':
        result['source_filename'] = version.original_filename
    return result


def _current(db, record, doc):
    if record is None or doc is None or doc.status != 'pending':
        return False
    try:
        return json.loads(record.scope_json) == _freeze(db, doc)
    except HTTPException:
        return False


def _state_expression():
    """用结构化管理信息比较避免对全库逐份加载标签、历史或计算文件哈希。"""
    current = and_(
        func.json_extract(PreorganizeProposal.scope_json, '$.version_id') == Document.current_version_id,
        func.json_extract(PreorganizeProposal.scope_json, '$.content_hash') == Version.sha256_hash,
        PreorganizeProposal.metadata_json == literal_column(_CURRENT_METADATA_SQL),
    )
    return case((current, PreorganizeProposal.state), else_='waiting')


def _detail(db, doc, record):
    from backend.app.services.object_skills import object_kind
    valid = _current(db, record, doc)
    scope = json.loads(record.scope_json) if record else {}
    result = json.loads(record.result_json or '{}') if valid and record else {}
    version = db.get(Version, doc.current_version_id) if doc.current_version_id else None
    return {'document_id': doc.id, 'state': record.state if valid else 'waiting',
            'object_kind': object_kind(version.original_filename, is_folder=doc.is_folder) if version else 'unknown',
            'phase': record.phase if valid and record.state == 'processing' else None,
            'proposal_id': record.id if valid else None,
            'error': record.error if valid else ('资料内容或管理信息已变化，请重新预整理' if record else None),
            'proposal': result.get('proposal') if valid and record.state == 'ready' else None,
            'sources': result.get('sources', []), 'progress': result.get('progress', {}),
            'usage': result.get('usage', {}), 'gaps': result.get('proposal', {}).get('gaps', []),
            'diagnostics': sanitize_diagnostics(result.get('diagnostics'), 'preorganize'),
            'source_version': {'id': scope.get('version_id'), 'number': scope.get('version_number'),
                               'content_hash': scope.get('content_hash')} if scope else None,
            'updated_at': record.updated_at.isoformat() if record else None}


def get_document(document_id):
    with database.SessionLocal() as db:
        doc = _document(db, document_id)
        return _detail(db, doc, db.get(PreorganizeProposal, document_id))


def list_documents(search='', state='', page=1, page_size=30):
    with database.SessionLocal() as db:
        expression = _state_expression()
        query = db.query(Document).outerjoin(Version, and_(Version.id == Document.current_version_id,
            Version.document_id == Document.id)).outerjoin(PreorganizeProposal,
                PreorganizeProposal.document_id == Document.id).filter(Document.status == 'pending')
        if search.strip():
            pattern = f'%{search.strip()}%'
            from backend.app.services.indexing import content_document_ids
            query = query.filter(or_(Document.name.ilike(pattern), Document.description.ilike(pattern),
                Document.tags.any(Tag.name.ilike(pattern)), Document.id.in_(content_document_ids(search))))
        counts = dict.fromkeys(STATES, 0)
        for value, count in query.with_entities(expression, func.count(Document.id)).group_by(expression).all():
            counts[value] = count
        filtered = query.filter(expression == state) if state else query
        total = filtered.count()
        docs = filtered.order_by(Document.created_at.desc(), Document.id).offset((page - 1) * page_size).limit(page_size).all()
        records = {item.document_id: item for item in db.query(PreorganizeProposal).filter(
            PreorganizeProposal.document_id.in_([doc.id for doc in docs])).all()} if docs else {}
        items = build_document_responses(docs, db)
        for item, doc in zip(items, docs):
            detail = _detail(db, doc, records.get(doc.id))
            item['preorganize'] = {key: detail[key] for key in (
                'state', 'phase', 'proposal_id', 'error', 'gaps', 'updated_at')}
        return {'items': items, 'total': total, 'counts': counts}


def _catalog(db):
    tag_names = [name for (name,) in db.query(Tag.name).order_by(Tag.name).limit(501).all()]
    tags = [name for name in tag_names[:500]
            if name != '待整理' and name.strip() and len(name) <= 100
            and not any(unicodedata.category(c).startswith('C') for c in name)]
    root = Path(settings.library_path).resolve()
    folders, queue = [], [root]
    roots = classification_folder_roots(db)
    # 有界宽度优先扫描；不进入整体资料、隐藏目录或待整理。
    visited = 0
    while queue and len(folders) < 200 and visited < 1000:
        parent = queue.pop(0)
        try:
            entries = sorted(parent.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            visited += 1
            if visited > 1000 or len(folders) >= 200:
                break
            if entry.name.startswith('.') or entry.is_symlink() or not entry.is_dir():
                continue
            relative = entry.relative_to(root).as_posix()
            if (len(relative) > 500 or '\\' in relative or ':' in relative
                    or any(unicodedata.category(c).startswith('C') for c in relative)):
                continue
            try:
                classification_directory(relative, db, folder_roots=roots)
            except HTTPException:
                continue
            folders.append(relative)
            queue.append(entry)
    return {'tags': tags, 'folders': folders,
            'limited': len(tag_names) > 500 or len(folders) >= 200 or visited >= 1000}


def _queue(db, doc, public, revision, catalog, automatic=False):
    scope = _freeze(db, doc)
    record = db.get(PreorganizeProposal, doc.id)
    if record is not None and _current(db, record, doc):
        if record.state == 'ready':
            return record
        if (record.state == 'processing' and record.service_revision == public['service_revision']
                and record.connection_revision == revision):
            if not automatic:
                record.automatic = False
            return record
    if record is None:
        record = PreorganizeProposal(document_id=doc.id, generation=0)
        db.add(record)
    record.id = generate_uuid()
    record.generation += 1
    record.state, record.phase = 'processing', 'queued'
    record.scope_json, record.metadata_json = _json(scope), _metadata_json(doc)
    record.catalog_json = _json(catalog)
    record.service_revision, record.connection_revision = public['service_revision'], revision
    record.automatic, record.result_json, record.error = automatic, None, None
    record.updated_at = utc_now()
    return record


def start(library_token, service_revision, consent, request_id, document_ids):
    _check_library(library_token)
    public, _, _, revision = _authorized(service_revision, consent)
    ids = list(dict.fromkeys(document_ids))
    receipt = _json({'document_ids': sorted(ids), 'service_revision': service_revision})
    with database.SessionLocal() as db:
        _begin(db, library_token)
        old = db.get(PreorganizeRequest, request_id)
        if old and old.input_json != receipt:
            raise HTTPException(409, '请求编号已用于另一批资料，请重新发起')
        catalog = _catalog(db) if not old else None
        items = []
        for document_id in ids:
            try:
                doc = _document(db, document_id)
                record = db.get(PreorganizeProposal, doc.id)
                if not old:
                    record = _queue(db, doc, public, revision, catalog)
                    db.flush()
                items.append(_detail(db, doc, record))
            except HTTPException as exc:
                items.append({'document_id': document_id, 'state': 'failed', 'error': _error_message(exc)})
        if not old:
            db.add(PreorganizeRequest(id=request_id, input_json=receipt))
        db.commit()
        return {'items': items}


def set_policy(library_token, service_revision=None, consent=False, enabled=False):
    _check_library(library_token)
    public, _, _, revision = _authorized(service_revision, consent) if enabled else (None, None, None, None)
    cancelled = set()
    with database.SessionLocal() as db:
        _begin(db, library_token)
        policy = db.get(PreorganizePolicy, 1)
        if policy is None:
            policy = PreorganizePolicy(id=1)
            db.add(policy)
        policy.enabled, policy.updated_at = enabled, utc_now()
        if enabled:
            policy.service_revision, policy.connection_revision = public['service_revision'], revision
        else:
            cancelled = {(_binding(), row.document_id, row.id, row.generation)
                for row in db.query(PreorganizeProposal.document_id, PreorganizeProposal.id,
                    PreorganizeProposal.generation).filter(
                        PreorganizeProposal.automatic.is_(True), PreorganizeProposal.state == 'processing').all()}
            db.execute(update(PreorganizeProposal).where(
                PreorganizeProposal.automatic.is_(True), PreorganizeProposal.state == 'processing').values(
                    state='waiting', phase='disabled', generation=PreorganizeProposal.generation + 1,
                    error='自动预整理已关闭', updated_at=utc_now()))
        db.commit()
    _cancel_runner(cancelled)
    return get_config()


def _cancel_runner(expected=None):
    """只取消事务实际撤销的执行实例，避免误停其后启动或接管的任务。"""
    entry = _running
    if entry is not None:
        if expected is not None and (
            entry['binding'], entry['document_id'], entry['id'], entry['generation']
        ) not in expected:
            return
        task = entry['task']
        loop = task.get_loop()
        if not loop.is_closed() and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # 关闭中的 loop；激活恢复器会标记遗留任务。


def cancel(library_token, document_ids):
    cancelled = set()
    with database.SessionLocal() as db:
        _begin(db, library_token)
        items = []
        for document_id in dict.fromkeys(document_ids):
            try:
                doc = _document(db, document_id)
                record = db.get(PreorganizeProposal, doc.id)
                if record:
                    cancelled.add((_binding(), record.document_id, record.id, record.generation))
                    record.state, record.phase, record.result_json = 'waiting', None, None
                    record.generation += 1
                    record.error, record.updated_at = '已取消，可再次启动预整理', utc_now()
                items.append(_detail(db, doc, record))
            except HTTPException as exc:
                items.append({'document_id': document_id, 'state': 'failed', 'error': _error_message(exc)})
        db.commit()
    _cancel_runner(cancelled)
    return {'items': items}


def _discover():
    public, _, _, revision = _connection()
    with database.SessionLocal() as db:
        _begin(db)
        policy = db.get(PreorganizePolicy, 1)
        if not _policy_payload(policy, public, revision)['active']:
            return
        rows = (db.query(Document).outerjoin(Version, and_(Version.id == Document.current_version_id,
            Version.document_id == Document.id)).outerjoin(PreorganizeProposal,
            PreorganizeProposal.document_id == Document.id).filter(Document.status == 'pending',
            or_(PreorganizeProposal.document_id.is_(None),
                PreorganizeProposal.phase == 'disabled',
                func.json_extract(PreorganizeProposal.scope_json, '$.version_id').is_distinct_from(Document.current_version_id),
                PreorganizeProposal.metadata_json.is_distinct_from(literal_column(_CURRENT_METADATA_SQL)),
                func.json_extract(PreorganizeProposal.scope_json, '$.content_hash').is_distinct_from(Version.sha256_hash)))
            .order_by(Document.created_at, Document.id).limit(100).all())
        catalog = _catalog(db) if rows else None
        for doc in rows:
            try:
                _queue(db, doc, public, revision, catalog, automatic=True)
            except HTTPException:
                continue  # 无保存版本不能发送；保存后下轮发现。
        db.commit()


def _claim():
    from backend.app.services.card_context import understanding_context
    public, config, key, revision = _connection()
    with database.SessionLocal() as db:
        _begin(db)
        record = db.query(PreorganizeProposal).filter_by(state='processing', phase='queued').order_by(
            PreorganizeProposal.updated_at, PreorganizeProposal.document_id).first()
        if record is None:
            return None
        doc = db.get(Document, record.document_id)
        error = None
        if not _current(db, record, doc):
            error = '资料已变化或已手工整理，请刷新后重新预整理'
        elif not config or record.service_revision != public['service_revision'] or record.connection_revision != revision:
            error = 'AI 服务配置已变化，请重新启动预整理'
        if error:
            record.state, record.phase, record.error = 'failed', None, error
            record.updated_at = utc_now()
            db.commit()
            return {'skipped': True}
        record.phase, record.updated_at = 'running', utc_now()
        job = {'binding': _binding(), 'engine': db.get_bind(), 'document_id': doc.id,
               'id': record.id, 'generation': record.generation, 'scope': json.loads(record.scope_json),
               'catalog': json.loads(record.catalog_json), 'automatic': record.automatic,
               'config': {**config, 'thinking_mode': 'disabled'}, 'key': key, 'revision': revision,
               'understanding': understanding_context(db, doc.id)}
        db.commit()
        return job


def _guard(job):
    if _binding() != job['binding']:
        raise AIError(409, 'PREORGANIZE_LIBRARY_CHANGED', '资料库已切换，本次预整理已停止')
    _, _, revision = ai_settings.get_connection()
    if revision != job['revision']:
        raise AIError(409, 'PREORGANIZE_SERVICE_CHANGED', 'AI 服务配置已变化，请重新预整理')
    with database.SessionLocal() as db:
        record = db.get(PreorganizeProposal, job['document_id'])
        doc = db.get(Document, job['document_id'])
        if (_binding() != job['binding'] or record is None or record.id != job['id']
                or record.generation != job['generation'] or record.state != 'processing'
                or not _current(db, record, doc)):
            raise AIError(409, 'PREORGANIZE_CHANGED', '资料或任务已变化，本次预整理已停止')


def _finish(job, result=None, error=None, diagnostics=None):
    diagnostics = sanitize_diagnostics(diagnostics or (result or {}).get('diagnostics'), 'preorganize')
    if result is not None and not isinstance(result.get('proposal'), dict):
        result, error = None, error or 'AI 未能形成有效整理方案，请重试或手工整理'
    if _binding() != job['binding']:
        return
    with database.SessionLocal() as db:
        _begin(db)
        record = db.get(PreorganizeProposal, job['document_id'])
        doc = db.get(Document, job['document_id'])
        if (record is None or record.id != job['id'] or record.generation != job['generation']
                or record.state != 'processing' or _binding() != job['binding']):
            return
        if not _current(db, record, doc):
            error, result = '资料已变化，请重新预整理', None
            diagnostics = {}
        # 配置更新与接受结果具有明确先后：持同一配置锁完成最终核验和提交，
        # 避免最后一次工具 guard 与落库之间改服务/清密钥仍接受旧结果。
        # 锁顺序为 DB 写事务 → AI 配置锁；配置模块不取得数据库或切库锁。
        with ai_settings._lock:
            if result is not None or diagnostics:
                try:
                    _, _, revision = ai_settings.get_connection()
                except ai_settings.AISettingsError:
                    revision = None
                if revision != job['revision']:
                    error, result = 'AI 服务配置已变化，请重新预整理', None
                    diagnostics = {}
            record.state, record.phase = ('ready' if result is not None else 'failed'), None
            if result is not None and result.get('card') is not None:
                from backend.app.services.document_cards import save_card
                save_card(db, doc.id, job['scope']['version_id'], job['scope']['content_hash'],
                          result['card'], result['sources'], result.get('coverage', {}),
                          job['config']['model'], commit=False)
            if result is not None:
                result = {**result, 'diagnostics': diagnostics}
            elif diagnostics:
                result = {'diagnostics': diagnostics}
            record.result_json = _json(result) if result is not None else None
            record.error, record.updated_at = error, utc_now()
            db.commit()
            log_diagnostics(logger, 'preorganize', record.state, diagnostics)


def _error_message(exc):
    from backend.app.core.partner_budget import PartnerPaused
    if isinstance(exc, PartnerPaused):
        return '本次预整理达到处理上限，尚未形成有效方案；资料保持待整理，可重试或手工整理，已有资料卡保持不变'
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail if isinstance(detail, str) else detail.get('error', {}).get('message', '操作失败，请重试')
    if isinstance(exc, AIError):
        return str(exc)
    return '预整理未完成，请检查 AI 服务后重试；本次不会自动重复调用'


async def process_one():
    global _running
    if not settings.library_path or not _process_lock.acquire(blocking=False):
        return False
    lease = False
    job = None
    diagnostics = StructuredDiagnostics('preorganize')
    try:
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
        task = asyncio.current_task()
        _running = {'task': task, 'binding': job['binding'], 'document_id': job['document_id'],
                    'id': job['id'], 'generation': job['generation']}
        output = await run_agent(
            message='请阅读这份资料并提出便于用户核对的命名、标签与分类方案，最后提交 propose_organization。',
            history=[], scope={job['document_id']: job['scope']}, config=job['config'], key=job['key'],
            engine=job['engine'], root=job['binding'][0], guard=lambda: _guard(job), emit=diagnostics,
            limits=dict(LIMITS), scope_mode='selected', purpose='preorganize',
            purpose_context={'document_id': job['document_id'], 'tags': job['catalog']['tags'],
                             'folders': job['catalog']['folders'], **job['understanding']})
        diagnostics.accept_output(output)
        await asyncio.to_thread(_guard, job)
        # 引擎已验证真实来源；宿主仍拒绝缺少结构化结果或范围外来源的返回值。
        proposal = output.get('proposal')
        sources = output.get('sources', [])
        if (not isinstance(proposal, dict) or not proposal.get('source_ids')
                or not sources or any(source.get('document_id') != job['document_id'] for source in sources)
                or not set(proposal['source_ids']).issubset({source.get('id') for source in sources})):
            raise AIError(502, 'PREORGANIZE_INCOMPLETE', 'AI 未能形成有原文依据的整理方案，请重试或手工整理')
        from backend.app.services.organization_tools import OrganizationProposal
        proposal = OrganizationProposal.model_validate(proposal).model_dump()
        _validate_fields(proposal, job['scope'])
        if proposal['target_dir'] not in ['', *job['catalog']['folders']]:
            raise AIError(502, 'PREORGANIZE_FOLDER_INVALID', 'AI 返回了未提供的分类目录，请重试')
        if job['catalog'].get('limited'):
            note = '分类语境达到本次上限，仅提供部分已有标签或分类目录，建议可能未覆盖全部分类。'
            proposal['gaps'] = list(dict.fromkeys([note, *proposal['gaps']]))[:30]
        result = {key: output.get(key, {} if key in ('progress', 'usage') else []) for key in
                  ('sources', 'progress', 'usage')}
        result['proposal'] = proposal
        result['card'] = output.get('card') or proposal.get('card')
        result['coverage'] = output.get('coverage', {})
        await asyncio.to_thread(_finish, job, result, diagnostics=diagnostics.value)
        return True
    except asyncio.CancelledError:
        if job and not job.get('skipped'):
            await asyncio.to_thread(_finish, job, error='预整理已中断，请主动重试；此前请求可能已计费',
                                    diagnostics=diagnostics.value)
        raise
    except Exception as exc:
        if job and not job.get('skipped'):
            await asyncio.to_thread(_finish, job, error=_error_message(exc), diagnostics=diagnostics.value)
            return True
        raise
    finally:
        _running = None
        if lease:
            _paid_call.release()
        _process_lock.release()


def _validate_fields(values, scope):
    name, tags, folder = values.get('name'), values.get('tags'), values.get('target_dir')
    if (not isinstance(name, str) or not name.strip() or name != name.strip()
            or len(name) > 200 or len(name.encode('utf-8')) > 255 or name in ('.', '..')
            or any(c in name for c in '/\\:') or any(unicodedata.category(c).startswith('C') for c in name)):
        raise HTTPException(422, '请输入有效资料名称，不能包含目录或控制字符')
    if not scope['is_folder'] and Path(name).suffix.lower() != Path(scope['name']).suffix.lower():
        raise HTTPException(422, '资料名称应保留原文件扩展名')
    if not scope['is_folder']:
        for suffix in ('.tar.gz', '.tar.bz2', '.tar.xz'):
            if scope['name'].lower().endswith(suffix) and not name.lower().endswith(suffix):
                raise HTTPException(422, '压缩包名称应保留完整的复合扩展名')
    if (not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(tag, str)
            or len(tag) > 100 or any(unicodedata.category(c).startswith('C') for c in tag) for tag in tags)):
        raise HTTPException(422, '标签最多 20 个，每个名称最多 100 字符')
    if (not isinstance(folder, str) or len(folder) > 500
            or any(unicodedata.category(c).startswith('C') for c in folder)):
        raise HTTPException(422, '分类目录格式不正确')


def _document_snapshot(db, doc):
    version = db.get(Version, doc.current_version_id)
    return {'name': doc.name, 'tags': sorted(tag.name for tag in doc.tags),
            'tag_records': [{'id': tag.id, 'name': tag.name} for tag in sorted(doc.tags, key=lambda tag: tag.id)],
            'storage_path': doc.storage_path, 'storage_area': doc.storage_area, 'status': doc.status,
            'version_id': doc.current_version_id, 'content_hash': version.sha256_hash if version else None,
            'metadata_hash': metadata_fingerprint(doc)}


def _check_working(doc, digest):
    source = document_path(doc)
    if not source.exists() or source.is_symlink():
        raise HTTPException(409, '工作文件缺失或路径已变化，请重新定位后重试')
    if doc.is_folder:
        from backend.app.services.snapshots import _build_folder_manifest
        manifest, _ = _build_folder_manifest(source)
        current = hashlib.sha256(manifest).hexdigest()
    else:
        before = source.stat()
        current = calculate_sha256(str(source))
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise HTTPException(409, '工作文件仍在编辑，请保存版本后重试')
    if current != digest:
        raise HTTPException(409, '工作文件有尚未保存的更改，请先保存版本并重新预整理')


def _apply_one(library_token, item):
    fields = {key: item[key] for key in ('document_id', 'proposal_id', 'request_id', 'name', 'tags', 'target_dir')}
    fields['tags'] = list(dict.fromkeys(tag.strip() for tag in fields['tags']))
    serialized = _json(fields)
    with database.SessionLocal() as db:
        _begin(db, library_token)
        operation = db.query(PreorganizeOperation).filter_by(request_id=fields['request_id']).first()
        if operation:
            if operation.input_json != serialized:
                raise HTTPException(409, '该请求已采用不同的方案，请刷新后重试')
            return {'document_id': fields['document_id'], 'ok': True, 'operation_id': operation.id}
        doc = _document(db, fields['document_id'])
        record = db.get(PreorganizeProposal, doc.id)
        if not _current(db, record, doc) or record.state != 'ready' or record.id != fields['proposal_id']:
            raise HTTPException(409, '整理方案已过期或不可用，请重新预整理')
        scope = json.loads(record.scope_json)
        _validate_fields(fields, scope)
        if not any(fields['tags']):
            raise HTTPException(400, '请至少添加一个有效标签后完成整理')
        target = classification_directory(fields['target_dir'], db, allow_root=True)
        if not target.is_dir():
            raise HTTPException(409, '分类目录不存在，请重新选择')
        _check_working(doc, scope['content_hash'])
        before = _document_snapshot(db, doc)
        with working_changes(db) as move:
            tags = get_or_create_tags(db, fields['tags'])
            organize_working(doc, fields['target_dir'], fields['name'], tags, move)
            _check_working(doc, scope['content_hash'])
            operation = PreorganizeOperation(request_id=fields['request_id'], document_id=doc.id,
                document_name=doc.name, input_json=serialized, before_json=_json(before),
                after_json=_json(_document_snapshot(db, doc)))
            db.add(operation)
            record.state, record.phase, record.updated_at = 'waiting', None, utc_now()
            record_change(db, 'document', doc.id, 'update', {'status': 'organized', 'source': 'ai_preorganize'})
        return {'document_id': fields['document_id'], 'ok': True, 'operation_id': operation.id}


def apply(library_token, items):
    _check_library(library_token)
    results = []
    for item in items:
        try:
            results.append(_apply_one(library_token, item))
        except (HTTPException, OSError) as exc:
            results.append({'document_id': item['document_id'], 'ok': False, 'error': _error_message(exc)})
    return {'items': results}


def operations():
    with database.SessionLocal() as db:
        rows = db.query(PreorganizeOperation).order_by(
            PreorganizeOperation.applied_at.desc(), PreorganizeOperation.id.desc()).limit(10).all()
        return {'items': [{'id': row.id, 'document_id': row.document_id, 'document_name': row.document_name,
            'applied_at': row.applied_at.isoformat(), 'undone_at': row.undone_at.isoformat() if row.undone_at else None}
            for row in rows]}


def undo(library_token, operation_id):
    with database.SessionLocal() as db:
        _begin(db, library_token)
        operation = db.get(PreorganizeOperation, operation_id)
        if operation is None:
            raise HTTPException(404, '整理记录不存在')
        if operation.undone_at:
            return {'operation_id': operation.id, 'undone': True}
        doc = db.get(Document, operation.document_id)
        before, after = json.loads(operation.before_json), json.loads(operation.after_json)
        if doc is None or _document_snapshot(db, doc) != after:
            raise HTTPException(409, '资料在整理后已发生变化，不能撤销以免覆盖后续修改')
        original_tags = []
        for saved_tag in before['tag_records']:
            tag = db.get(Tag, saved_tag['id'])
            if tag is None or tag.name != saved_tag['name']:
                raise HTTPException(409, '整理前的标签已删除或改名，不能撤销以免恢复过时分类')
            original_tags.append(tag)
        _check_working(doc, after['content_hash'])
        target = working_location(before['storage_path'], before['storage_area'])
        with working_changes(db) as move:
            move(document_path(doc), target)
            for field in ('name', 'storage_path', 'storage_area', 'status'):
                setattr(doc, field, before[field])
            doc.tags = original_tags
            _check_working(doc, before['content_hash'])
            operation.undone_at = utc_now()
            record = db.get(PreorganizeProposal, doc.id)
            if record:
                record.state, record.phase, record.result_json = 'waiting', None, None
                record.error, record.updated_at = '已撤销本次整理，可手工整理或重新预整理', utc_now()
                record.generation += 1
            record_change(db, 'document', doc.id, 'update', {'status': 'pending', 'source': 'undo_ai_preorganize'})
        return {'operation_id': operation.id, 'undone': True}


def initialize():
    """库激活时只恢复排队记录，运行中的未知付费任务不得自动重放。"""
    entry = _running
    binding = _binding()
    if entry and entry['binding'] != binding:
        _cancel_runner()
    with database.SessionLocal() as db:
        statement = update(PreorganizeProposal).where(
            PreorganizeProposal.state == 'processing', PreorganizeProposal.phase == 'running')
        if entry and entry['binding'] == binding and not entry['task'].done():
            statement = statement.where(PreorganizeProposal.document_id != entry['document_id'])
        db.execute(statement.values(state='failed', phase=None, updated_at=utc_now(),
            error='上次预整理在退出或切库时中断，请主动重试；此前请求可能已计费'))
        db.commit()


async def _worker_loop():
    while True:
        try:
            # 每份独立子任务，取消单份不终止队列。
            worked = await asyncio.create_task(process_one())
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            worked = False
        except Exception:
            logger.exception('预整理队列检查失败，将在下轮检查；未自动重放运行中的付费请求')
            worked = False
        await asyncio.sleep(.15 if worked else 2)


def start_worker():
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_worker_loop(), name='teamhub-preorganize')


async def stop_worker():
    global _worker
    if _worker and not _worker.done():
        _worker.cancel()
        await asyncio.gather(_worker, return_exceptions=True)
    _worker = None
