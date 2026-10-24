"""按轮冻结授权的伙伴任务。模型调用期间不持有资料库数据库会话。"""
import asyncio
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time

from fastapi import HTTPException
from sqlalchemy import text, update

from backend.app import database
from backend.app.api.ai import PREPARATION_TTL, _error, _paid_call
from backend.app.config import settings
from backend.app.core import ai_settings
from backend.app.core.ai_client import AIError
from backend.app.core.hermes_agent import ENGINE, run_agent
from backend.app.core.partner_budget import (CHAT_BUDGET, CHAT_SEGMENT_LIMITS, FIELDS,
                                            PAUSE_MESSAGES, PartnerPaused, normalize_budget)
from backend.app.models import Document, Version, generate_uuid, utc_now
from backend.app.partner_models import PartnerMessage, PartnerSession, PartnerTask
from backend.app.services.indexing import metadata_fingerprint
from backend.app.services.partner_context import authorization_for_task, build_context, dependencies_for

ACTIVE = ('queued', 'running')
UNFINISHED = (*ACTIVE, 'paused')
LIMITS = {key: normalize_budget()[key] for key in FIELDS}  # 兼容本机SDK测试/旧调用；每轮使用授权的entry.limits。
_lock = threading.RLock()
_preparations = OrderedDict()
_running = {}
MAX_ANSWER_CHARACTERS = 64000


def has_pending_interactive_tasks():
    """后台队列让出新聊天；只读取锁内内存，不取得资料库锁。"""
    with _lock:
        return any(not entry['cancel_event'].is_set()
                   and (entry.get('task') is None or not entry['task'].done())
                   for entry in _running.values())


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _root():
    if not settings.library_path:
        raise _error(400, 'PARTNER_LIBRARY_REQUIRED', '请先打开个人资料库')
    return str(Path(settings.library_path).resolve())


def _binding():
    return (_root(), id(database.engine), str(database.engine.url))


def _timestamp(value):
    return value.isoformat() if value else None


def _session(db, session_id):
    session = db.get(PartnerSession, session_id)
    if session is None:
        raise _error(404, 'PARTNER_SESSION_NOT_FOUND', '对话不存在，请刷新后重试')
    return session


def _task(db, task_id):
    task = db.get(PartnerTask, task_id)
    if task is None:
        raise _error(404, 'PARTNER_TASK_NOT_FOUND', '任务不存在，请刷新后重试')
    return task


def _session_json(db, session):
    active = db.query(PartnerTask.id).filter(PartnerTask.session_id == session.id,
                                           PartnerTask.state.in_(UNFINISHED)).first()
    return {'id': session.id, 'title': session.title, 'created_at': _timestamp(session.created_at),
            'updated_at': _timestamp(session.updated_at), 'active_task_id': active[0] if active else None}


def _task_json(task):
    result = json.loads(task.result_json or '{}')
    continuations = result.get('continuations', [])
    last_request_id = continuations[-1]['request_id'] if continuations else task.request_id
    running = _running.get(task.id)
    preview = (running.get('answer_preview') if running and task.state in ACTIVE
               and running['binding'] == _binding() else result.get('answer_preview'))
    return {'id': task.id, 'session_id': task.session_id, 'request_id': task.request_id,
            'last_request_id': last_request_id,
            'state': task.state, 'message': task.message,
            'answer': result.get('answer'), 'answer_preview': preview, 'steps': result.get('steps', []),
            'preview_discarded': result.get('preview_discarded', False),
            'warnings': result.get('warnings', []),
            'sources': result.get('sources', []), 'artifacts': result.get('artifacts', []),
            'error': json.loads(task.error_json) if task.error_json else None,
            'pause': result.get('pause'), 'usage': result.get('usage', {}),
            'segment_usage': result.get('segment_usage', {}), 'progress': result.get('progress', {}),
            'budget': result.get('budget', {}), 'limits': result.get('limits', {}),
            'created_at': _timestamp(task.created_at), 'updated_at': _timestamp(task.updated_at)}


def _chat_options(tasks):
    if tasks:
        original = json.loads(tasks[0].input_json or '{}')
        mode = original.get('scope_mode', 'selected')
        return {'scope_mode': mode,
                'document_ids': list(original.get('scope', {})) if mode == 'selected' else [],
                'thinking_mode': original.get('service', {}).get('thinking_mode', 'disabled')}
    return {'scope_mode': 'selected', 'document_ids': [], 'thinking_mode': 'disabled'}


def create_session(title='新对话'):
    _root()
    title = title.strip()
    if not title or len(title) > 120:
        raise _error(422, 'PARTNER_INVALID_TITLE', '对话名称须为 1–120 字')
    with database.SessionLocal() as db:
        session = PartnerSession(title=title)
        db.add(session)
        db.commit()
        return _session_json(db, session)


def list_sessions():
    _root()
    with database.SessionLocal() as db:
        sessions = db.query(PartnerSession).order_by(PartnerSession.updated_at.desc()).limit(100).all()
        return {'items': [_session_json(db, session) for session in sessions]}


def get_session(session_id):
    _root()
    with database.SessionLocal() as db:
        session = _session(db, session_id)
        messages = db.query(PartnerMessage).filter_by(session_id=session_id).order_by(
            PartnerMessage.created_at.asc(), PartnerMessage.id.asc()).all()
        tasks = db.query(PartnerTask).filter_by(session_id=session_id).order_by(
            PartnerTask.created_at.desc()).limit(50).all()
        return {'session': _session_json(db, session), 'messages': [
            {'id': message.id, 'role': message.role, 'content': message.content,
             'task_id': message.task_id, 'sources': _message_sources(message),
             'created_at': _timestamp(message.created_at)} for message in messages],
                'tasks': [_task_json(task) for task in tasks], 'chat_options': _chat_options(tasks)}


def get_request(session_id, request_id):
    """只读核对原发送请求；404表示此刻尚未登记，不触发新任务或模型重试。"""
    _root()
    with database.SessionLocal() as db:
        _session(db, session_id)
        task = db.query(PartnerTask).filter_by(session_id=session_id, request_id=request_id).first()
        if task is None:
            raise _error(404, 'PARTNER_REQUEST_NOT_FOUND', '当前尚未找到此发送请求，可继续核对')
        return _task_json(task)


def delete_session(session_id):
    _root()
    with _lock, database.SessionLocal() as db:
        session = _session(db, session_id)
        if db.query(PartnerTask.id).filter_by(session_id=session_id).filter(PartnerTask.state.in_(ACTIVE)).first():
            raise _error(409, 'PARTNER_SESSION_BUSY', '请先停止当前任务，再删除对话')
        db.delete(session)
        db.commit()
        for token in list(_preparations):
            if _preparations[token]['session_id'] == session_id:
                _preparations.pop(token)
    return {'deleted': True}


def _freeze(doc, version):
    return {'name': doc.name, 'version_id': version.id, 'version_number': version.version_number,
            'content_hash': version.sha256_hash, 'metadata_hash': metadata_fingerprint(doc),
            'is_folder': doc.is_folder}


def _scope_documents(db, scope_mode, document_ids):
    if scope_mode not in {'selected', 'library'}:
        raise _error(422, 'PARTNER_INVALID_SCOPE', '请选择本次可读取的资料范围')
    ids = list(dict.fromkeys(document_ids))
    if len(ids) > 100 or (scope_mode == 'library' and ids):
        raise _error(422, 'PARTNER_INVALID_SCOPE', '选中资料最多 100 份；整库模式无需同时指定资料')
    query = db.query(Document, Version).join(Version, (Version.id == Document.current_version_id)
            & (Version.document_id == Document.id)).filter(Document.status.in_(('pending', 'organized')))
    if scope_mode == 'selected':
        if not ids:
            return {}
        query = query.filter(Document.id.in_(ids))
    values = query.order_by(Document.name, Document.id).limit(10001).all()
    if len(values) > 10000:
        raise _error(422, 'PARTNER_SCOPE_TOO_LARGE', '首期每轮最多授权 10000 份资料，请缩小范围')
    if scope_mode == 'selected' and len(values) != len(ids):
        raise _error(409, 'PARTNER_DOCUMENT_CHANGED', '所选资料不存在、已删除或尚无保存版本，请重新选择')
    return {doc.id: _freeze(doc, version) for doc, version in values}


def _message_sources(message):
    stored = json.loads(message.sources_json)
    return stored.get('sources', []) if isinstance(stored, dict) else stored


def _message_dependencies(db, message):
    return dependencies_for(db, message)


def _context(db, session_id, scope):
    return build_context(db, session_id, scope, engine=db.get_bind(), root=_root())


def _thinking_mode(value):
    if value not in {'enabled', 'disabled'}:
        raise _error(422, 'PARTNER_INVALID_THINKING_MODE', '请选择本轮思考方式')
    return value


def _same_service(config, entry):
    # 思考方式按轮冻结；原AI配置revision仍独立核验，不写回全局设置。
    return (all(config[field] == entry['service'][field] for field in ('base_url', 'model'))
            and all(entry['service'].get(field) == value for field, value in ENGINE.items()))


def _service_identity(config):
    return {**{field: config[field] for field in ('base_url', 'model')}, **ENGINE}


def _service_revision(config):
    """公开修订值只描述外发目标，不包含密钥及其存储引用。"""
    return hashlib.sha256(_json(_service_identity(config)).encode()).hexdigest()


def chat_config():
    config = ai_settings.get_settings()
    configured = bool(config.get('has_api_key'))
    return {'service': _service_identity(config), 'service_revision': _service_revision(config),
            'configured': configured, 'has_api_key': configured,
            'defaults': {'scope_mode': 'selected', 'thinking_mode': 'disabled', 'budget': dict(CHAT_BUDGET)}}


def _checked_history(value):
    if not value:
        return []
    from backend.app.core.partner_history import validate_engine_history
    try:
        return validate_engine_history(value)
    except (ValueError, TypeError):
        return []


def _native_context(db, session_id, scope, service, context):
    """仅复用最新完整轮；跳过失效轮会让模型误以为用户最后一句不存在。"""
    previous = db.query(PartnerTask).filter_by(session_id=session_id).order_by(
        PartnerTask.created_at.desc(), PartnerTask.id.desc()).first()
    if previous is None or previous.state != 'completed':
        return [], {}
    original, result = json.loads(previous.input_json or '{}'), json.loads(previous.result_json or '{}')
    old_service = original.get('service', {})
    if any(old_service.get(field) != value for field, value in _service_identity(service).items()):
        return [], {}
    dependencies = result.get('dependency_records')
    if (not isinstance(dependencies, dict) or not result.get('engine_history_reusable', False)
            or any(scope.get(key) != record for key, record in dependencies.items())):
        return [], {}
    # 原生历史包含用户手工粘贴的文字。收窄前一轮授权集合时，使用逐消息核验的文字回退。
    authorization = authorization_for_task(previous)
    if authorization is None or not authorization.issubset(scope):
        return [], {}
    old_checkpoint = result.get('checkpoint') or {}
    current_checkpoint = context.get('checkpoint') or {}
    if (old_checkpoint.get('sources', []) != current_checkpoint.get('sources', [])
            or old_checkpoint.get('evidence', []) != current_checkpoint.get('evidence', [])):
        return [], {}
    history = _checked_history(result.get('engine_history'))
    return history, dependencies if history else {}


def _request_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _validate_send(message, document_ids, thinking_mode, budget, expected_service_revision, consent):
    if consent is not True:
        raise _error(400, 'AI_CONSENT_REQUIRED', '发送前需明确当前 AI 服务与资料范围')
    if not isinstance(message, str) or not message.strip() or len(message) > 8000:
        raise _error(422, 'PARTNER_INVALID_MESSAGE', '请输入不超过 8000 字的问题')
    if not isinstance(expected_service_revision, str) or not expected_service_revision:
        raise _error(409, 'AI_SERVICE_CHANGED', '请刷新当前 AI 服务信息后发送')
    return message.strip(), sorted(set(document_ids or [])), _thinking_mode(thinking_mode), normalize_budget(
        budget if budget is not None else CHAT_BUDGET)


def _reserve_runner(task_id, entry, engine):
    entry['cancel_event'] = threading.Event()
    running = {'task': None, 'binding': entry['binding'], 'engine': engine,
               'cancel_event': entry['cancel_event'], 'paid_lease': False, 'direct_chat': True,
               'scope_records': entry['scope_records'],
               'dependency_records': {**entry.get('history_dependencies', {}),
                   **entry.get('previous_dependencies', {}),
                   **(entry['scope_records'] if entry['scope']['mode'] == 'selected' else {})}}
    _running[task_id] = running
    return running


def _schedule(task_id, entry, config, key, revision, engine, running):
    runner = asyncio.create_task(_execute(task_id, entry, config, key, revision, engine))
    running['task'] = runner
    runner.add_done_callback(lambda done: _release_task(task_id, done))


async def send(session_id, *, message, request_id, expected_service_revision, consent=False,
               scope_mode='selected', document_ids=None, thinking_mode='disabled', budget=None):
    """一次发送即授权已展示的目标与范围；数据库登记先于排队和实际收费请求。"""
    message, ids, thinking, selected = _validate_send(
        message, document_ids, thinking_mode, budget, expected_service_revision, consent)
    fingerprint = _request_digest({'message': message, 'scope_mode': scope_mode, 'document_ids': ids,
        'thinking_mode': thinking, 'service_revision': expected_service_revision, 'budget': selected})
    binding = _binding()
    with _lock, database.SessionLocal() as db:
        _session(db, session_id)
        existing = db.query(PartnerTask).filter_by(session_id=session_id, request_id=request_id).first()
        if existing:
            if json.loads(existing.input_json).get('request_digest') != fingerprint:
                raise _error(409, 'PARTNER_REQUEST_REUSED', '同一请求编号不能用于不同消息或资料范围')
            return _task_json(existing)
    config, key, revision = await asyncio.to_thread(ai_settings.get_connection)
    if not key:
        raise _error(400, 'AI_NOT_CONFIGURED', '请先在设置中配置 AI 服务密钥')
    if _service_revision(config) != expected_service_revision:
        raise _error(409, 'AI_SERVICE_CHANGED', 'AI 服务已变化，请查看当前目标后重新发送')
    running = None
    with _lock:
        if _binding() != binding:
            raise _error(409, 'PARTNER_LIBRARY_CHANGED', '个人库已切换，请在当前资料库重新发送')
        try:
            with database.SessionLocal() as db:
                db.execute(text('BEGIN IMMEDIATE'))
                session = _session(db, session_id)
                existing = db.query(PartnerTask).filter_by(session_id=session_id, request_id=request_id).first()
                if existing:
                    if json.loads(existing.input_json).get('request_digest') != fingerprint:
                        raise _error(409, 'PARTNER_REQUEST_REUSED', '同一请求编号不能用于不同消息或资料范围')
                    return _task_json(existing)
                if db.query(PartnerTask.id).filter_by(session_id=session_id).filter(PartnerTask.state.in_(ACTIVE)).first():
                    raise _error(409, 'PARTNER_SESSION_BUSY', '当前回复仍在生成，请先停止后发送新问题')
                scope = _scope_documents(db, scope_mode, ids)
                context = _context(db, session_id, scope)
                native, native_dependencies = _native_context(db, session_id, scope, config, context)
                service = {**_service_identity(config), 'thinking_mode': thinking}
                limits = {field: selected[field] for field in FIELDS}
                entry = {'session_id': session_id, 'message': message, 'scope_records': scope,
                    'scope': {'mode': scope_mode}, 'history': context['history'], 'context': context['context'],
                    'checkpoint': context['checkpoint'], 'history_dependencies': {
                        **context['history_dependencies'], **native_dependencies},
                    'service': service, 'thinking_mode': thinking, 'budget': selected, 'limits': limits,
                    'binding': binding, 'config_revision': revision, 'direct_chat': True, 'engine_history': native}
                task = PartnerTask(session_id=session_id, request_id=request_id, preparation_id='direct:' + request_id,
                    state='queued', message=message, input_json=_json({'direct_chat': True,
                        'request_digest': fingerprint, 'scope_mode': scope_mode, 'scope': scope,
                        'authorization_scope_ids': sorted(set(scope) | set(context['authorization_scope_ids'])),
                        'history': entry['history'], 'history_dependencies': entry['history_dependencies'],
                        'context': entry['context'], 'service': service, 'budget': selected}),
                    result_json=_json({'budget': selected, 'limits': limits}))
                # 旧暂停轮保留成果，结束执行状态；用户可自然提出新的问题。
                for paused in db.query(PartnerTask).filter_by(session_id=session_id, state='paused').all():
                    paused.state, paused.updated_at = 'cancelled', utc_now()
                    paused.error_json = _json({'code': 'PARTNER_SUPERSEDED', 'message': '已开始新问题，原阶段成果已保留'})
                db.add(task)
                db.flush()
                db.add(PartnerMessage(session_id=session_id, task_id=task.id, role='user', content=message))
                session.updated_at = utc_now()
                if session.title == '新对话':
                    session.title = message[:40]
                engine, task_id = db.get_bind(), task.id
                running = _reserve_runner(task_id, entry, engine)
                db.commit()
                response = _task_json(task)
            _schedule(task_id, entry, config, key, revision, engine, running)
            return response
        except BaseException:
            if running is not None and running['task'] is None:
                _running.pop(task_id, None)
            raise


async def resume(task_id, *, request_id, expected_service_revision, consent=False, budget=None):
    """用户主动继续真正耗尽总预算的任务；沿用原目标，不更换服务或范围。"""
    if consent is not True:
        raise _error(400, 'AI_CONSENT_REQUIRED', '请确认继续当前任务')
    selected = normalize_budget(budget if budget is not None else CHAT_BUDGET)
    fingerprint = _request_digest({'service_revision': expected_service_revision, 'budget': selected})
    binding = _binding()
    config, key, revision = await asyncio.to_thread(ai_settings.get_connection)
    if _service_revision(config) != expected_service_revision:
        raise _error(409, 'AI_SERVICE_CHANGED', 'AI 服务已变化，请查看当前目标后重新发送问题')
    running = None
    with _lock:
        if _binding() != binding:
            raise _error(409, 'PARTNER_LIBRARY_CHANGED', '个人库已切换，请重新打开对话')
        try:
            with database.SessionLocal() as db:
                db.execute(text('BEGIN IMMEDIATE'))
                task = _task(db, task_id)
                original, result = json.loads(task.input_json), json.loads(task.result_json or '{}')
                previous = next((item for item in result.get('continuations', [])
                                 if item['request_id'] == request_id), None)
                if previous:
                    if previous.get('request_digest') != fingerprint:
                        raise _error(409, 'PARTNER_REQUEST_REUSED', '同一继续请求编号不能用于不同设置')
                    return _task_json(task)
                if task.state != 'paused' or not result.get('pause', {}).get('resumable'):
                    raise _error(409, 'PARTNER_NOT_RESUMABLE', '当前任务无需继续，请直接发送新问题')
                if not _same_service(config, original):
                    raise _error(409, 'AI_SERVICE_CHANGED', '原任务使用的服务已变化，请向当前服务重新发送问题')
                scope = original.get('scope', {})
                dependencies = result.get('dependency_records', scope)
                _validate_scope(db, dependencies)
                entry = {'session_id': task.session_id, 'message': task.message, 'scope_records': scope,
                    'scope': {'mode': original.get('scope_mode', 'selected')},
                    'history': original.get('history', []), 'context': original.get('context'),
                    'history_dependencies': original.get('history_dependencies', {}),
                    'service': original['service'], 'thinking_mode': original['service'].get('thinking_mode', 'disabled'),
                    'budget': selected, 'limits': {field: selected[field] for field in FIELDS},
                    'binding': binding, 'config_revision': revision, 'direct_chat': True,
                    'checkpoint': result.get('checkpoint'), 'previous_usage': result.get('usage', {}),
                    'previous_dependencies': dependencies, 'previous_artifacts': result.get('artifacts', []),
                    'previous_answer': result.get('answer', ''),
                    'engine_history': _checked_history(result.get('engine_history'))
                        if result.get('engine_history_reusable') else []}
                if not key:
                    raise _error(400, 'AI_NOT_CONFIGURED', '请先在设置中配置 AI 服务密钥')
                result.setdefault('continuations', []).append({'request_id': request_id, 'request_digest': fingerprint})
                result.update(budget=selected, limits=entry['limits'], pause=None, segment_usage={})
                task.result_json, task.error_json, task.state, task.updated_at = _json(result), None, 'queued', utc_now()
                engine = db.get_bind()
                running = _reserve_runner(task_id, entry, engine)
                db.commit()
                response = _task_json(task)
            _schedule(task_id, entry, config, key, revision, engine, running)
            return response
        except BaseException:
            if running is not None and running['task'] is None:
                _running.pop(task_id, None)
            raise


def prepare(session_id, message, scope_mode='selected', document_ids=None, budget=None, thinking_mode='enabled'):
    if not isinstance(message, str) or not message.strip() or len(message) > 8000:
        raise _error(422, 'PARTNER_INVALID_MESSAGE', '请输入不超过 8000 字的问题')
    message = message.strip()
    thinking_mode = _thinking_mode(thinking_mode)
    binding = _binding()
    with database.SessionLocal() as db:
        _session(db, session_id)
        if db.query(PartnerTask.id).filter_by(session_id=session_id).filter(PartnerTask.state.in_(UNFINISHED)).first():
            raise _error(409, 'PARTNER_SESSION_BUSY', '当前对话有未结束任务，请先继续或结束当前任务')
        scope = _scope_documents(db, scope_mode, document_ids or [])
        context = _context(db, session_id, scope)
    config = ai_settings.get_settings()
    try:
        connection, _, revision = ai_settings.get_connection()
        config = connection
    except ai_settings.AISettingsError:
        # 无密钥时仍可在本机准备预览；配置密钥后须重新准备并确认。
        revision = None
    service = {**{field: config[field] for field in ('base_url', 'model')}, **ENGINE,
               'thinking_mode': thinking_mode}
    documents = [{'id': key, **{field: value[field] for field in
                  ('name', 'version_id', 'version_number', 'content_hash')}} for key, value in scope.items()][:100]
    public_scope = {'mode': scope_mode, 'documents': documents, 'count': len(scope)}
    if len(scope) > 100:
        public_scope['preview_truncated'] = True
    selected_budget = normalize_budget(budget)
    notes = [
        '本轮会把问题、下方历史对话，以及按需读取的授权资料原文、PDF 页面或内嵌图片发送给所示 AI 服务。',
        '读取范围固定为本次确认的已保存版本；工作副本未保存的修改不会发送。',
        f'本段最多 {selected_budget["model_requests"]} 次模型请求、{selected_budget["tool_calls"]} 次工具调用、'
        f'{selected_budget["output_tokens"]} 输出 tokens、{selected_budget["time_seconds"]} 秒；预算耗尽保存进度，继续须再次确认。联网失败不自动重试。',
        '生成成果先作为草稿展示，点击保存后才收录为新的待整理资料。',
    ]
    if scope_mode == 'library':
        notes.append('整库授权包括当前待整理和文档库的资料；确认后新增或更新的资料不会扩大本次范围。')
    if context['omitted']:
        notes.append(f'有 {context["omitted"]} 条来源已不在本轮有效范围内的历史回答未发送。')
    if context['context']['evidence']:
        notes.append('下方已读原文与来源会按原编号发送，范围仅限本次仍获授权的固定版本；此前草稿不会作为本轮新成果。')
    public = {'preparation_id': secrets.token_urlsafe(32), 'session_id': session_id,
              'message': message, 'scope': public_scope, 'history': context['history'], 'service': service,
              'thinking_mode': thinking_mode, 'context': context['context'],
              'budget': selected_budget, 'limits': {key: selected_budget[key] for key in FIELDS},
              'notes': notes, 'expires_in': PREPARATION_TTL}
    entry = {**public, 'scope_records': scope, 'history_dependencies': context['history_dependencies'],
             'checkpoint': context['checkpoint'], 'context_revision': context['context_revision'],
             'config_revision': revision, 'binding': binding, 'created': time.monotonic()}
    with _lock:
        for token in list(_preparations):
            if time.monotonic() - _preparations[token]['created'] >= PREPARATION_TTL:
                _preparations.pop(token)
        while len(_preparations) >= 16:
            _preparations.popitem(last=False)
        _preparations[public['preparation_id']] = entry
    return public


def _validate_scope(db, scope, document_ids=None):
    ids = list(document_ids) if document_ids is not None else list(scope)
    if not ids:
        return
    # SQLite 变量个数有限，整库确认按小批读取；不展开未授权资料。
    found = {}
    for start in range(0, len(ids), 400):
        pairs = db.query(Document, Version).join(Version, (Version.id == Document.current_version_id)
            & (Version.document_id == Document.id)).filter(Document.id.in_(ids[start:start + 400]),
              Document.status.in_(('pending', 'organized'))).all()
        found.update({doc.id: _freeze(doc, version) for doc, version in pairs})
    if any(found.get(key) != scope.get(key) for key in ids):
        raise _error(409, 'PARTNER_DOCUMENT_CHANGED', '授权资料或其版本已变化，请重新准备本轮任务')


def _guard(entry, revision):
    if entry.get('cancel_event') is not None and entry['cancel_event'].is_set():
        raise _error(409, 'PARTNER_CANCELLED', '本轮任务已停止')
    if _binding() != entry['binding']:
        raise _error(409, 'PARTNER_LIBRARY_CHANGED', '个人库已切换，本轮任务已停止，请重新确认')
    if ai_settings.get_connection()[2] != revision:
        raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 服务设置已变化，本轮结果未采用，请重新确认')


async def start(session_id, preparation_id, consent, request_id):
    if consent is not True:
        raise _error(400, 'AI_CONSENT_REQUIRED', '请先确认本轮问题、历史和资料外发范围')
    with _lock:
        with database.SessionLocal() as db:
            _session(db, session_id)
            existing = db.query(PartnerTask).filter_by(session_id=session_id, request_id=request_id).first()
            if existing:
                if existing.preparation_id != preparation_id:
                    raise _error(409, 'PARTNER_REQUEST_REUSED', '同一请求编号不能用于不同任务')
                return _task_json(existing)
        entry = _preparations.get(preparation_id)
        if (entry is None or entry['session_id'] != session_id
                or time.monotonic() - entry['created'] >= PREPARATION_TTL):
            raise _error(409, 'AI_PREPARATION_EXPIRED', '本轮授权预览已过期，请重新准备并确认')
        config, key, revision = ai_settings.get_connection()
        if not key:
            raise _error(400, 'AI_NOT_CONFIGURED', '请先在设置中配置 AI 服务密钥')
        if not _same_service(config, entry):
            raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 服务已变化，请重新准备并确认')
        if entry['config_revision'] != revision:
            raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 密钥或配置已变化，请重新准备并确认')
        _guard(entry, revision)
        if not _paid_call.acquire(blocking=False):
            raise _error(429, 'AI_BUSY', '已有 AI 请求正在处理，请稍后再试')
        running_entry = None
        try:
            with database.SessionLocal() as db:
                session = _session(db, session_id)
                if db.query(PartnerTask.id).filter_by(session_id=session_id).filter(PartnerTask.state.in_(UNFINISHED)).first():
                    raise _error(409, 'PARTNER_SESSION_BUSY', '当前对话仍有任务正在执行')
                _validate_scope(db, entry['scope_records'])
                current_context = _context(db, session_id, entry['scope_records'])
                if (current_context['context_revision'] != entry['context_revision']
                        or current_context['history'] != entry['history']
                        or current_context['context'] != entry['context']
                        or current_context['history_dependencies'] != entry['history_dependencies']):
                    raise _error(409, 'PARTNER_HISTORY_CHANGED', '对话已变化，请重新准备并确认')
                task = PartnerTask(session_id=session_id, request_id=request_id, preparation_id=preparation_id,
                    state='queued', message=entry['message'], input_json=_json({
                        'scope_mode': entry['scope']['mode'], 'scope': entry['scope_records'],
                        'history': entry['history'], 'history_dependencies': entry['history_dependencies'],
                        'context': entry['context'], 'service': entry['service'], 'budget': entry['budget']}),
                    result_json=_json({'budget': entry['budget'], 'limits': entry['limits']}))
                db.add(task)
                db.flush()
                db.add(PartnerMessage(session_id=session_id, task_id=task.id, role='user', content=entry['message']))
                session.updated_at = utc_now()
                if session.title == '新对话':
                    session.title = entry['message'][:40]
                # 在提交前登记预约，幂等激活也不能把“刚提交、尚未调度”的本轮当作崩溃遗留。
                engine = db.get_bind()
                entry = {**entry, 'cancel_event': threading.Event()}
                task_id = task.id
                running_entry = {'task': None, 'binding': entry['binding'], 'engine': engine,
                                 'cancel_event': entry['cancel_event']}
                _running[task_id] = running_entry
                db.commit()
                response = _task_json(task)
            _preparations.pop(preparation_id, None)
            runner = asyncio.create_task(_execute(task_id, entry, config, key, revision, engine))
            running_entry['task'] = runner
            # 协程首次被调度前也可能取消，释放不能只依赖协程内部 finally。
            runner.add_done_callback(lambda done: _release_task(task_id, done))
            return response
        except BaseException:
            if running_entry is not None and running_entry['task'] is None:
                _running.pop(task_id, None)
            _paid_call.release()
            raise


def _bound_db(engine):
    # 只在当前引擎匹配时打开短会话；禁止把旧任务写进新库。
    with database._active_sessions_lock:
        if engine is not database.engine:
            return None
        return database.SessionLocal()


def prepare_continue(task_id, budget=None, thinking_mode=None):
    """继续授权复用原冻结范围，证据留在本机，仅预览本段会发送的真实证据。"""
    binding = _binding()
    with database.SessionLocal() as db:
        task = _task(db, task_id)
        result, original = json.loads(task.result_json or '{}'), json.loads(task.input_json)
        if task.state != 'paused' or not result.get('pause', {}).get('resumable'):
            raise _error(409, 'PARTNER_NOT_RESUMABLE', '此任务不能继续，请查看成果或重新提问')
        _validate_scope(db, original.get('scope', {}))
        if not isinstance(result.get('checkpoint'), dict):
            raise _error(409, 'PARTNER_CHECKPOINT_MISSING', '未找到完整阅读进度，请新建问题')
        session_id, message = task.session_id, task.message
    config, _, revision = ai_settings.get_connection()
    mode = _thinking_mode(thinking_mode if thinking_mode is not None else
                          original.get('service', {}).get('thinking_mode', 'enabled'))
    selected = normalize_budget(budget)
    scope = original['scope']
    documents = [{'id': key, **{field: record[field] for field in
                  ('name', 'version_id', 'version_number', 'content_hash')}} for key, record in scope.items()][:100]
    public = {'preparation_id': secrets.token_urlsafe(32), 'session_id': session_id, 'task_id': task_id,
        'continuation': True, 'message': message, 'scope': {'mode': original['scope_mode'],
        'count': len(scope), 'documents': documents, 'preview_truncated': len(scope) > 100},
        'service': {**{field: config[field] for field in ('base_url', 'model')}, **ENGINE, 'thinking_mode': mode},
        'thinking_mode': mode,
        'history': original.get('history', []), 'budget': selected,
        'limits': {key: selected[key] for key in FIELDS}, 'progress': result.get('progress', {}),
        'continuation_context': {'summary': result.get('answer', ''),
            'evidence': result['checkpoint'].get('evidence', []),
            'sources': result.get('sources', []), 'artifacts': result.get('artifacts', [])},
        'notes': ['继续沿用原问题和原授权资料的固定版本，不增加资料范围。',
            '会向所示服务发送原问题、授权历史、已保留的真实文字证据与阶段成果；图像未保存为模型上下文，必要时重新读取。',
            '这是新的独立预算，确认后才继续请求；已完成的文字读取进度会恢复，不把摘要当作全文。'],
        'expires_in': PREPARATION_TTL}
    entry = {**public, 'scope_records': scope, 'history_dependencies': original.get('history_dependencies', {}),
        'checkpoint': result['checkpoint'], 'previous_artifacts': result.get('artifacts', []),
        'previous_answer': result.get('answer', ''),
        'previous_usage': result.get('usage', {}), 'previous_dependencies': result.get('dependency_records', {}),
        'config_revision': revision, 'binding': binding, 'created': time.monotonic()}
    with _lock:
        for token in list(_preparations):
            if time.monotonic() - _preparations[token]['created'] >= PREPARATION_TTL:
                _preparations.pop(token)
        while len(_preparations) >= 16:
            _preparations.popitem(last=False)
        _preparations[public['preparation_id']] = entry
    return public


async def continue_task(task_id, preparation_id, consent, request_id):
    if consent is not True:
        raise _error(400, 'AI_CONSENT_REQUIRED', '请确认继续处理的范围、证据与本段预算')
    with _lock:
        with database.SessionLocal() as db:
            task = _task(db, task_id)
            previous = json.loads(task.result_json or '{}')
            existing = next((item for item in previous.get('continuations', []) if item['request_id'] == request_id), None)
            if existing:
                if existing['preparation_id'] != preparation_id:
                    raise _error(409, 'PARTNER_REQUEST_REUSED', '同一继续请求编号不能重复用于其他授权')
                return _task_json(task)
            if task.state != 'paused':
                raise _error(409, 'PARTNER_NOT_RESUMABLE', '任务当前不能继续，请刷新状态')
        entry = _preparations.get(preparation_id)
        if (not entry or entry.get('task_id') != task_id or not entry.get('continuation')
                or time.monotonic() - entry['created'] >= PREPARATION_TTL):
            raise _error(409, 'AI_PREPARATION_EXPIRED', '继续授权预览已过期，请重新准备')
        config, key, revision = ai_settings.get_connection()
        if entry['config_revision'] != revision or not _same_service(config, entry):
            raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 服务已变化，请重新准备并确认')
        _guard(entry, revision)
        if not key:
            raise _error(400, 'AI_NOT_CONFIGURED', '请先配置 AI 服务密钥')
        if not _paid_call.acquire(blocking=False):
            raise _error(429, 'AI_BUSY', '已有 AI 请求正在处理，请稍后再试')
        running_entry = None
        try:
            with database.SessionLocal() as db:
                task = _task(db, task_id)
                if task.state != 'paused':
                    raise _error(409, 'PARTNER_NOT_RESUMABLE', '任务当前不能继续')
                _validate_scope(db, entry['scope_records'])
                result = json.loads(task.result_json or '{}')
                if result.get('checkpoint') != entry['checkpoint']:
                    raise _error(409, 'PARTNER_CHECKPOINT_CHANGED', '任务进度已变化，请重新准备')
                if result.get('answer', '') != entry['previous_answer']:
                    raise _error(409, 'PARTNER_CHECKPOINT_CHANGED', '阶段结论已变化，请重新预览并确认')
                # 预览后用户可能保存了草稿；保留最新的保存标识，不能被继续请求的旧快照覆盖。
                entry = {**entry, 'previous_artifacts': result.get('artifacts', []),
                         'previous_usage': result.get('usage', {})}
                result.setdefault('continuations', []).append({'request_id': request_id, 'preparation_id': preparation_id})
                result.update(budget=entry['budget'], limits=entry['limits'], pause=None, segment_usage={})
                original = json.loads(task.input_json)
                original['service'] = entry['service']
                task.input_json = _json(original)
                task.state, task.updated_at, task.error_json = 'queued', utc_now(), None
                task.result_json = _json(result)
                engine = db.get_bind()
                entry = {**entry, 'cancel_event': threading.Event()}
                running_entry = {'task': None, 'binding': entry['binding'], 'engine': engine,
                                 'cancel_event': entry['cancel_event']}
                _running[task_id] = running_entry
                db.commit()
                response = _task_json(task)
            _preparations.pop(preparation_id, None)
            runner = asyncio.create_task(_execute(task_id, entry, config, key, revision, engine))
            running_entry['task'] = runner
            runner.add_done_callback(lambda done: _release_task(task_id, done))
            return response
        except BaseException:
            if running_entry is not None and running_entry['task'] is None:
                _running.pop(task_id, None)
            _paid_call.release()
            raise


def _release_task(task_id, runner):
    with _lock:
        entry = _running.get(task_id)
        if entry is not None and entry['task'] is runner:
            entry['cancel_event'].set()
            _running.pop(task_id)
            if entry.get('paid_lease', True):
                _paid_call.release()


def _step(task_id, engine, kind, message):
    if kind == 'dependency_records':
        # 宿主工具桥的私有状态通知；资料原文、模型历史与依赖对象不进入公开 steps。
        with _lock:
            running = _running.get(task_id)
            if (running is not None and running.get('direct_chat') and running['engine'] is engine
                    and engine is database.engine and not running['cancel_event'].is_set()
                    and isinstance(message, dict)
                    and all(running['scope_records'].get(key) == record for key, record in message.items())):
                running['dependency_records'].update(message)
        return
    if kind == 'answer_preview':
        # 未完成来源核验的生成文本仅保存在当前库/任务的内存预约中。
        with _lock:
            running = _running.get(task_id)
            if (running is not None and running['engine'] is engine and engine is database.engine
                    and running['binding'] == _binding() and not running['cancel_event'].is_set()):
                limit = MAX_ANSWER_CHARACTERS if running.get('direct_chat') else 18000
                running['answer_preview'] = str(message)[:limit]
        return
    session = _bound_db(engine)
    if session is None:
        return
    with session as db:
        task = db.get(PartnerTask, task_id)
        if task is None or task.state not in ACTIVE:
            return
        result = json.loads(task.result_json)
        steps = result.setdefault('steps', [])
        if len(steps) < 100:
            steps.append({'id': generate_uuid(), 'kind': kind, 'message': message[:300], 'at': _timestamp(utc_now())})
        task.result_json = _json(result)
        task.updated_at = utc_now()
        db.commit()


def _preserve_preview(db, task, result, running, preview, error=None):
    if not json.loads(task.input_json or '{}').get('direct_chat'):
        return
    discarded = bool(error and error.get('code') in {
        'PARTNER_DOCUMENT_CHANGED', 'PARTNER_LIBRARY_CHANGED', 'AI_CONFIGURATION_CHANGED', 'PARTNER_TASK_CHANGED'})
    dependencies = (running.get('dependency_records', {}) if running else
                    result.get('dependency_records', {}))
    if not discarded:
        try:
            _validate_scope(db, dependencies)
        except HTTPException:
            discarded = True
    if discarded:
        result.update(answer_preview='', preview_discarded=True)
    elif preview:
        result.update(answer_preview=preview[:MAX_ANSWER_CHARACTERS], preview_discarded=False)


def _finish(task_id, engine, state, *, output=None, error=None, scope=None, read_document_ids=None,
            dependency_records=None):
    preview = None
    with _lock:
        running = _running.get(task_id)
        if running is not None and running['engine'] is engine:
            preview = running.pop('answer_preview', None)
    session = _bound_db(engine)
    if session is None:
        return
    with session as db:
        # 完成结果与来源校验在同一短写事务中，阻止检查后资料被并发删除/换版。
        db.execute(text('BEGIN IMMEDIATE'))
        task = db.get(PartnerTask, task_id)
        if task is None or task.state not in ACTIVE:
            return
        result = json.loads(task.result_json or '{}')
        if output:
            _validate_scope(db, scope or {}, read_document_ids or [])
            _validate_scope(db, dependency_records or {})
            output = dict(output)
            native = _checked_history(output.get('engine_history'))
            output['engine_history'] = native
            output['engine_history_reusable'] = bool(native) and output.get('engine_history_reusable', True)
            result.update(output)
            result['dependency_records'] = dependency_records or {}
            if state == 'completed':
                result['pause'] = None
            if state == 'completed':
                result.pop('answer_preview', None)
                result.pop('preview_discarded', None)
                db.add(PartnerMessage(session_id=task.session_id, task_id=task.id, role='assistant',
                    content=output['answer'], sources_json=_json({'schema_version': 2,
                        'sources': output.get('sources', []), 'dependencies': dependency_records or {},
                        'authorization_scope_ids': sorted(authorization_for_task(task) or set())})))
        if state != 'completed':
            _preserve_preview(db, task, result, running, preview, error)
        task.result_json = _json(result)
        task.state, task.updated_at = state, utc_now()
        task.error_json = _json(error) if error else None
        db.commit()


async def _acquire_queued_lease(task_id, entry, revision):
    """新聊天可等待后台索引；取消等待者不能释放别人持有的共享收费租约。"""
    started = time.monotonic()
    notified = False
    while True:
        await asyncio.to_thread(_guard, entry, revision)
        with _lock:
            running = _running.get(task_id)
            if running is None or running['cancel_event'].is_set():
                raise asyncio.CancelledError
            if running.get('paid_lease', True):
                return
            if _paid_call.acquire(blocking=False):
                running['paid_lease'] = True
                return
        if not notified:
            _step(task_id, running['engine'], 'status', '正在等待前一个 AI 请求结束，可随时停止')
            notified = True
        if time.monotonic() - started >= entry['limits']['time_seconds']:
            raise AIError(429, 'AI_QUEUE_TIMEOUT', '等待前一个 AI 请求超时，本条消息尚未发送给模型，请稍后重试')
        await asyncio.sleep(.1)


def _output_dependencies(output, entry):
    read_ids = output.pop('read_document_ids', [source['document_id'] for source in output.get('sources', [])])
    dependencies = {**entry.get('history_dependencies', {}), **entry.get('previous_dependencies', {}),
                    **output.pop('dependency_records', {})}
    if not entry.get('direct_chat'):
        # 旧接口仍向旧引擎提供其已预览名称；新聊天全库范围不预览任意文件。
        dependencies.update({key: entry['scope_records'][key] for key in list(entry['scope_records'])[:30]})
    for key in read_ids:
        if key not in entry['scope_records']:
            raise _error(409, 'PARTNER_DOCUMENT_CHANGED', '回答使用了范围外的资料，本轮结果未采用')
        dependencies[key] = entry['scope_records'][key]
    if any(entry['scope_records'].get(key) != record for key, record in dependencies.items()):
        raise _error(409, 'PARTNER_DOCUMENT_CHANGED', '回答依赖的资料已不在当前允许范围内')
    return read_ids, dependencies


def _segment_progress(output, entry):
    old_checkpoint, new_checkpoint = entry.get('checkpoint') or {}, output.get('checkpoint') or {}
    if (new_checkpoint.get('evidence') != old_checkpoint.get('evidence')
            or new_checkpoint.get('sources') != old_checkpoint.get('sources')
            or output.get('artifacts', []) != entry.get('previous_artifacts', [])):
        return True
    history = _checked_history(output.get('engine_history')) if output.get('engine_history_reusable') else []
    return bool(history) and history != entry.get('engine_history')


def _retain_segment(task_id, engine, output, dependencies):
    session = _bound_db(engine)
    if session is None:
        return False
    with session as db:
        db.execute(text('BEGIN IMMEDIATE'))
        task = db.get(PartnerTask, task_id)
        if task is None or task.state not in ACTIVE:
            return False
        _validate_scope(db, dependencies)
        result = json.loads(task.result_json or '{}')
        result.update(output)
        result['dependency_records'] = dependencies
        result['pause'] = None
        task.result_json, task.updated_at = _json(result), utc_now()
        db.commit()
        return True


async def _execute(task_id, entry, config, key, revision, engine):
    try:
        await _acquire_queued_lease(task_id, entry, revision)
        session = _bound_db(engine)
        if session is None:
            return
        with session as db:
            task = _task(db, task_id)
            if task.state != 'queued':
                return
            task.state, task.updated_at = 'running', utc_now()
            db.commit()
        _step(task_id, engine, 'status', '正在思考')
        started = time.monotonic()
        run_usage = {field: 0 for field in FIELDS}
        while True:
            limits = dict(entry['limits'])
            if entry.get('direct_chat'):
                limits = {field: max(1, int(entry['limits'][field] - run_usage[field])) for field in FIELDS}
                limits['time_seconds'] = max(1, int(entry['limits']['time_seconds'] -
                    max(run_usage['time_seconds'], time.monotonic() - started)))
                for field, cap in CHAT_SEGMENT_LIMITS.items():
                    limits[field] = min(limits[field], cap)
            options = dict(message=entry['message'], history=entry['history'],
                history_dependencies=entry['history_dependencies'], scope=entry['scope_records'],
                config={**config, 'thinking_mode': entry['thinking_mode']}, key=key, engine=engine,
                root=entry['binding'][0], guard=lambda: _guard(entry, revision),
                emit=lambda kind, message: _step(task_id, engine, kind, message), limits=limits,
                checkpoint=entry.get('checkpoint'), previous_artifacts=entry.get('previous_artifacts', []),
                previous_answer=entry.get('previous_answer'), context=entry.get('context'))
            if entry.get('direct_chat'):
                options['scope_mode'] = entry['scope']['mode']
            if entry.get('engine_history'):
                options['engine_history'] = entry['engine_history']
            paused = None
            try:
                output = await run_agent(**options)
            except PartnerPaused as exc:
                paused = exc
                output = dict(exc.output)
            await asyncio.to_thread(_guard, entry, revision)
            read_ids, dependencies = _output_dependencies(output, entry)
            usage = dict(output.get('usage', {}))
            progress = _segment_progress(output, entry)
            native = _checked_history(output.get('engine_history'))
            output['engine_history'] = native
            output['engine_history_reusable'] = bool(native) and output.get('engine_history_reusable', True)
            for field in FIELDS:
                run_usage[field] += usage.get(field, 0)
            _merge_usage(output, entry)
            if paused is None:
                _finish(task_id, engine, 'completed', output=output, scope=entry['scope_records'],
                        read_document_ids=read_ids, dependency_records=dependencies)
                return
            remaining = all(run_usage[field] < entry['limits'][field] for field in FIELDS)
            remaining = remaining and time.monotonic() - started < entry['limits']['time_seconds']
            if (entry.get('direct_chat') and paused.reason in CHAT_SEGMENT_LIMITS and remaining and progress
                    and usage.get('model_requests', 0) + usage.get('tool_calls', 0) > 0):
                if not _retain_segment(task_id, engine, output, dependencies):
                    return
                entry = {**entry, 'checkpoint': output.get('checkpoint'),
                    'previous_artifacts': output.get('artifacts', []), 'previous_answer': output.get('answer', ''),
                    'previous_usage': output['usage'], 'previous_dependencies': dependencies,
                    'engine_history': native if output['engine_history_reusable'] else []}
                _step(task_id, engine, 'status', '正在继续整理已有资料并形成答复')
                continue
            message = PAUSE_MESSAGES[paused.reason]
            if entry.get('direct_chat') and paused.reason in FIELDS:
                message = '本次处理已达到任务总额度，已有内容已保留；可点击继续，或直接提出新问题。'
                if remaining and not progress:
                    message = '连续处理没有获得新的有效进展，已保留现有内容；可补充问题后继续。'
            output['pause'] = {'reason': paused.reason, 'message': message,
                               'resumable': paused.reason != 'evidence_limit'}
            _finish(task_id, engine, 'paused', output=output, scope=entry['scope_records'],
                    read_document_ids=read_ids, dependency_records=dependencies)
            return
    except asyncio.CancelledError:
        _finish(task_id, engine, 'cancelled', error={'code': 'PARTNER_CANCELLED',
            'message': '本轮任务已停止，已生成的内容保留为未完成答复'})
    except HTTPException as exc:
        error = exc.detail.get('error') if isinstance(exc.detail, dict) else None
        _finish(task_id, engine, 'failed', error={key: error[key] for key in ('code', 'message')} if error else {
            'code': 'PARTNER_TASK_CHANGED', 'message': '任务条件已变化，请查看资料或服务设置后继续'})
    except AIError as exc:
        _finish(task_id, engine, 'failed', error={'code': exc.code, 'message': str(exc)})
    except Exception:
        # 不记录原始模型消息、图片、工具参数或上游错误，避免资料和密钥进入日志。
        _finish(task_id, engine, 'failed', error={'code': 'PARTNER_EXECUTION_FAILED',
            'message': '本轮处理意外中断，已生成的内容已保留。请重试或检查 AI 服务状态'})
    finally:
        # Terminal state is visible before done callbacks run. Release here so
        # immediate continuation can acquire the lease; the callback still covers
        # cancellation before this coroutine is first scheduled.
        _release_task(task_id, asyncio.current_task())


def get_task(task_id):
    return _event_snapshot(task_id)[1]


def _event_snapshot(task_id, binding=None):
    """核对库绑定与预约短会话须原子完成，阻止同UUID从切换后的库读出。"""
    with database._active_sessions_lock:
        current = _binding()
        if binding is not None and current != binding:
            raise _error(409, 'PARTNER_LIBRARY_CHANGED', '个人库已切换，请重新打开任务')
        # SessionLocal在此锁内绑定当前engine；TrackedSession计数接续阻止切库，
        # 读取期间无需持锁，也不会形成 active_sessions_lock → 伙伴_lock 的锁序。
        session = database.SessionLocal()
    with session as db:
        snapshot = _task_json(_task(db, task_id))
    return current, snapshot


def _event_binding_matches(binding):
    with database._active_sessions_lock:
        try:
            return _binding() == binding
        except HTTPException:
            return False


def event_stream(task_id):
    """在返回HTTP响应前校验任务存在与库绑定，避免在响应头之后才报404。"""
    binding, initial = _event_snapshot(task_id)
    return task_events(task_id, binding=binding, initial=initial)


async def task_events(task_id, *, binding=None, initial=None):
    """只变化时发送完整快照；监听断开只结束此生成器，不取消后台任务。"""
    if binding is None:
        with database._active_sessions_lock:
            binding = _binding()
    previous = None
    heartbeat = time.monotonic()
    while True:
        if not _event_binding_matches(binding):
            yield 'event: done\ndata: ' + _json({'reason': 'PARTNER_LIBRARY_CHANGED'}) + '\n\n'
            return
        try:
            task = initial if initial is not None else _event_snapshot(task_id, binding)[1]
        except HTTPException as exc:
            detail = exc.detail.get('error', {}) if isinstance(exc.detail, dict) else {}
            reason = ('PARTNER_LIBRARY_CHANGED' if detail.get('code') == 'PARTNER_LIBRARY_CHANGED'
                      or not _event_binding_matches(binding) else 'PARTNER_TASK_NOT_FOUND')
            yield 'event: done\ndata: ' + _json({'reason': reason}) + '\n\n'
            return
        initial = None
        payload = _json(task)
        # 序列化之后到实际推送之前再次核对；此处不保留数据库会话或锁。
        if not _event_binding_matches(binding):
            yield 'event: done\ndata: ' + _json({'reason': 'PARTNER_LIBRARY_CHANGED'}) + '\n\n'
            return
        if payload != previous:
            yield 'event: task\ndata: ' + payload + '\n\n'
            previous = payload
            heartbeat = time.monotonic()
        elif time.monotonic() - heartbeat >= 15:
            yield ': heartbeat\n\n'
            heartbeat = time.monotonic()
        if task['state'] not in ACTIVE:
            if not _event_binding_matches(binding):
                yield 'event: done\ndata: ' + _json({'reason': 'PARTNER_LIBRARY_CHANGED'}) + '\n\n'
                return
            yield 'event: done\ndata: ' + _json({'id': task_id, 'state': task['state']}) + '\n\n'
            return
        # 每次get_task自行关闭短数据库会话，等待期间不占库。
        await asyncio.sleep(.2)


def _merge_usage(output, entry):
    segment = output.get('usage', {})
    output['segment_usage'] = segment
    output['usage'] = {key: round(entry.get('previous_usage', {}).get(key, 0) + segment.get(key, 0), 3)
                       for key in FIELDS}
    output['usage']['input_tokens'] = entry.get('previous_usage', {}).get('input_tokens', 0) + segment.get('input_tokens', 0)
    output['usage']['token_usage_complete'] = (entry.get('previous_usage', {}).get('token_usage_complete', True)
                                              and segment.get('token_usage_complete', True))
    output['usage']['unmetered_requests'] = (entry.get('previous_usage', {}).get('unmetered_requests', 0)
                                             + segment.get('unmetered_requests', 0))
    output['budget'], output['limits'] = entry['budget'], entry['limits']


async def cancel(task_id):
    _root()
    with _lock:
        with database.SessionLocal() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            task = _task(db, task_id)
            if task.state not in UNFINISHED:
                return _task_json(task)
            running = _running.get(task_id)
            if running and running['binding'] == _binding() and running.get('direct_chat'):
                stored = json.loads(task.result_json or '{}')
                _preserve_preview(db, task, stored, running, running.get('answer_preview'))
                task.result_json = _json(stored)
            task.state = 'cancelled'
            task.updated_at = utc_now()
            task.error_json = _json({'code': 'PARTNER_CANCELLED', 'message': '本轮任务已停止，已生成的内容保留为未完成答复'})
            db.commit()
            result = _task_json(task)
        running = _running.get(task_id)
        if running and running['binding'] == _binding():
            running.pop('answer_preview', None)
            running['cancel_event'].set()
            if running['task'] is not None:
                running['task'].cancel()
    return result


def save_artifact(task_id, artifact_id, consent, request_id):
    if consent is not True:
        raise _error(400, 'PARTNER_SAVE_CONSENT_REQUIRED', '请确认将此草稿保存为新的待整理资料')
    _root()
    with _lock, database.SessionLocal() as db:
        # 来源核验与收录的首个提交处于同一短写事务，阻止其他API在检查后换版/改名。
        db.execute(text('BEGIN IMMEDIATE'))
        task = _task(db, task_id)
        if task.state not in ('completed', 'paused', 'failed', 'cancelled', 'interrupted'):
            raise _error(409, 'PARTNER_TASK_NOT_COMPLETE', '任务尚未完成，无法保存草稿')
        result = json.loads(task.result_json)
        original = json.loads(task.input_json or '{}')
        # 兼容首版未单独保存依赖的已完成草稿，保守复核其原冻结范围。
        _validate_scope(db, result.get('dependency_records', original.get('scope', {})))
        artifact = next((item for item in result.get('artifacts', []) if item['id'] == artifact_id), None)
        if artifact is None:
            raise _error(404, 'PARTNER_ARTIFACT_NOT_FOUND', '草稿不存在')
        if artifact.get('saved_document_id'):
            return {'document_id': artifact['saved_document_id'], 'artifact_id': artifact_id, 'duplicate': artifact.get('duplicate', False)}
        if any(item.get('save_request_id') == request_id and item['id'] != artifact_id for item in result.get('artifacts', [])):
            raise _error(409, 'PARTNER_REQUEST_REUSED', '此保存请求编号已用于其他草稿')
        from backend.app.api.documents import _ingest_document
        from backend.app.api.schemas import DocumentCreate
        from fastapi.responses import JSONResponse
        # 仅把已保存的模型草稿写入隔离临时目录，不接受任意路径和扩展名。
        name = artifact['name']
        if Path(name).name != name or Path(name).suffix.lower() not in {'.md', '.txt'}:
            raise _error(422, 'PARTNER_INVALID_ARTIFACT', '仅支持保存 Markdown 或文本草稿')
        content = artifact.get('content')
        if (not isinstance(content, str) or not content.strip() or len(content) > 30000
                or hashlib.sha256(content.encode('utf-8')).hexdigest() != artifact.get('content_hash')):
            raise _error(409, 'PARTNER_ARTIFACT_CHANGED', '草稿内容校验失败，尚未收录任何资料')
        with tempfile.TemporaryDirectory(prefix='teamhub-partner-save-') as directory:
            source = Path(directory) / name
            source.write_text(artifact['content'], encoding='utf-8')
            document = _ingest_document(DocumentCreate(name=name, source_path=str(source), tags=['AI生成'], storage_mode='copy'), db, pending=True)
        duplicate = isinstance(document, JSONResponse)
        document_id = json.loads(document.body)['document']['id'] if duplicate else document['id']
        # _ingest_document 自行提交；保存编号随后与结果一并提交。再次执行收录会内容查重，不增加冗余资料。
        task = _task(db, task_id)
        artifact.update(saved_document_id=document_id, save_request_id=request_id, duplicate=duplicate)
        task.result_json = _json(result)
        db.commit()
        return {'document_id': document_id, 'artifact_id': artifact_id, 'duplicate': duplicate}


def initialize():
    """每次激活恢复遗留；同绑定真实任务保留，旧绑定取消且不重放付费调用。

    此函数可能在 library_change 锁内调用，禁止取得伙伴 _lock 形成反向锁序。
    CPython 下快照字典，不等待旧任务；短会话在激活临界区完成状态恢复。
    """
    binding = (str(Path(settings.library_path).resolve()) if settings.library_path else '',
               id(database.engine), str(database.engine.url))
    entries = tuple(_running.items())
    active_ids = []
    for task_id, entry in entries:
        runner = entry['task']
        if entry['binding'] == binding and (runner is None or not runner.done()):
            active_ids.append(task_id)
        elif entry['binding'] != binding:
            entry['cancel_event'].set()
            if runner is not None and not runner.done():
                loop = runner.get_loop()
                if not loop.is_closed():
                    try:
                        loop.call_soon_threadsafe(runner.cancel)
                    except RuntimeError:
                        # 退出同时关闭 loop；持久任务由激活恢复，不另起模型调用。
                        pass
    with database.SessionLocal() as db:
        statement = update(PartnerTask).where(PartnerTask.state.in_(ACTIVE))
        if active_ids:
            statement = statement.where(PartnerTask.id.notin_(active_ids))
        db.execute(statement.values(
            state='interrupted', updated_at=utc_now(), error_json=_json({
                'code': 'PARTNER_INTERRUPTED', 'message': '上次任务在应用退出或切库时中断，请重新确认后执行'})))
        db.commit()


async def shutdown():
    with _lock:
        entries = list(_running.items())
    for task_id, entry in entries:
        entry['cancel_event'].set()
        if entry['task'] is not None and entry['task'].done():
            _release_task(task_id, entry['task'])
    tasks = [entry['task'] for _, entry in entries if entry['task'] is not None and not entry['task'].done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    from backend.app.core.hermes_agent import shutdown as shutdown_hermes
    await shutdown_hermes()
    with _lock:
        _preparations.clear()
