"""个人库内容索引及经逐次确认的 AI 检索线索增强。"""
import json
import secrets
import time
from collections import OrderedDict
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.api.ai import (
    AnalyzeRequest, PREPARATION_TTL, SafeAIRoute, _error, _local_request, _paid_call, _scope,
)
from backend.app.core import ai_settings
from backend.app.core.index_ai_client import complete_index
from backend.app.database import get_db
from backend.app.models import Document
from backend.app.services import indexing

router = APIRouter(prefix='/api/ai/indexes', tags=['资料索引'], route_class=SafeAIRoute,
                   dependencies=[Depends(_local_request)])
_preparations = OrderedDict()
_lock = RLock()
MAX_PREVIEW_CHARS = 12000
MAX_PREVIEW_UNITS = 32


def _record(db, document_id):
    try:
        record = indexing.load_record(db, document_id)
    except ValueError:
        raise _error(404, 'INDEX_DOCUMENT_UNAVAILABLE', '资料不存在或暂不可用') from None
    if record is None or not record.get('units'):
        raise _error(409, 'INDEX_NOT_READY', '暂无可供 AI 分析的正文索引，请先完成本机解析')
    return record


def _identity(record):
    return (record['version_id'], record['content_hash'], record['generation'], record['metadata_hash'])


def _check(entry, db):
    if entry['scope'] != _scope():
        raise _error(409, 'AI_PREPARATION_EXPIRED', '个人库已切换，请重新准备索引内容')
    db.expire_all()
    try:
        record = _record(db, entry['document_id'])
    except HTTPException:
        raise _error(409, 'INDEX_CHANGED', '资料或索引已变化，请重新准备并确认') from None
    if _identity(record) != entry['identity']:
        raise _error(409, 'INDEX_CHANGED', '资料、版本或整理信息已变化，请重新准备并确认')
    return record


@router.get('/{document_id}')
def get_index(document_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
              db: Session = Depends(get_db)):
    try:
        return indexing.status(db, document_id, offset=offset, limit=limit)
    except ValueError:
        raise _error(404, 'INDEX_DOCUMENT_UNAVAILABLE', '资料不存在或暂不可用') from None


@router.post('/{document_id}/rebuild')
def rebuild_index(document_id: str, db: Session = Depends(get_db)):
    try:
        return indexing.rebuild(db, document_id)
    except ValueError:
        raise _error(404, 'INDEX_DOCUMENT_UNAVAILABLE', '资料不存在或暂不可用') from None


@router.post('/{document_id}/prepare')
def prepare_index(document_id: str, db: Session = Depends(get_db)):
    record = _record(db, document_id)
    doc = db.get(Document, document_id)
    # 展示文本就是发给模型的完整资料负载；不在确认后偷偷添加背景信息。
    payload = {'metadata': {'name': doc.name, 'tags': [tag.name for tag in doc.tags][:50],
                            'classification': doc.storage_path or ''}, 'units': []}
    for unit in record['units'][:MAX_PREVIEW_UNITS]:
        candidate = {key: unit[key] for key in ('id', 'kind', 'label', 'location', 'context', 'text')}
        payload['units'].append(candidate)
        if len(json.dumps(payload, ensure_ascii=False)) > MAX_PREVIEW_CHARS:
            payload['units'].pop()
            break
    if not payload['units']:
        raise _error(422, 'INDEX_PREVIEW_TOO_LARGE', '首个内容单元过大，暂无法生成有界 AI 预览')
    preview = json.dumps(payload, ensure_ascii=False)
    config = ai_settings.get_settings()
    truncated = len(payload['units']) < len(record['units']) or record['state'] != 'ready'
    notes = [f"本次基于已保存版本，仅发送 {len(payload['units'])}/{len(record['units'])} 个已提取内容单元。",
             'AI 结果作为辅助检索线索保存，不改变人工标签、原文件或版本。']
    if truncated:
        notes.append('本次 AI 只处理展示的部分内容，不能代表整份资料已经被理解。')
    public = {
        'preparation_id': secrets.token_urlsafe(32), 'document_id': document_id,
        'document_name': doc.name, 'version_id': record['version_id'],
        'content_hash': record['content_hash'], 'text': preview, 'truncated': truncated,
        'notes': notes, 'expires_in': PREPARATION_TTL,
        'service': {'base_url': config['base_url'], 'model': config['model']},
    }
    entry = {**public, 'scope': _scope(), 'identity': _identity(record),
             'unit_ids': [unit['id'] for unit in payload['units']],
             'created': time.monotonic(), 'result': None}
    with _lock:
        for token in list(_preparations):
            if time.monotonic() - _preparations[token]['created'] >= PREPARATION_TTL:
                _preparations.pop(token)
        while len(_preparations) >= 16:
            _preparations.popitem(last=False)
        _preparations[public['preparation_id']] = entry
    return public


@router.post('/enrich')
def enrich_index(body: AnalyzeRequest, db: Session = Depends(get_db)):
    if body.consent is not True:
        raise _error(400, 'AI_CONSENT_REQUIRED', '请先确认允许发送展示的索引内容')
    with _lock:
        entry = _preparations.get(body.preparation_id)
        if entry is None or time.monotonic() - entry['created'] >= PREPARATION_TTL:
            _preparations.pop(body.preparation_id, None)
            raise _error(409, 'AI_PREPARATION_EXPIRED', '索引预览已过期，请重新准备并确认')
    _check(entry, db)
    config, key, revision = ai_settings.get_connection()
    if {field: config[field] for field in ('base_url', 'model')} != entry['service']:
        raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 服务已变化，请重新准备并确认')
    if entry['result'] is not None:
        return entry['result']
    if not _paid_call.acquire(blocking=False):
        raise _error(429, 'AI_BUSY', '已有 AI 请求正在处理，请稍候')
    try:
        with _lock:
            if (_preparations.get(body.preparation_id) is not entry
                    or time.monotonic() - entry['created'] >= PREPARATION_TTL):
                raise _error(409, 'AI_PREPARATION_EXPIRED', '本次索引任务已结束，请重新准备并确认')
            if entry['result'] is not None:
                return entry['result']
        generated = complete_index(config, key, filename=entry['document_name'],
                                   text=entry['text'], unit_ids=entry['unit_ids'])
        _check(entry, db)
        if ai_settings.get_connection()[2] != revision:
            raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 配置已变化，本次结果未采用，请重新确认')
        try:
            indexing.save_ai(db, entry['document_id'], entry['version_id'], entry['content_hash'],
                             entry['identity'][3], {**generated, 'model': config['model'],
                                                    'processed_units': len(entry['unit_ids'])},
                             expected_generation=entry['identity'][2])
        except ValueError:
            raise _error(409, 'INDEX_CHANGED', '资料或索引已变化，本次结果未采用') from None
        result = {key: entry[key] for key in ('preparation_id', 'document_id', 'version_id', 'content_hash')}
        result.update(generated, model=config['model'])
        with _lock:
            entry['result'] = result
        return result
    except Exception:
        # 付费调用不自动重试，失败任务不允许被排队中的旧请求再次发送。
        with _lock:
            _preparations.pop(body.preparation_id, None)
        raise
    finally:
        _paid_call.release()
