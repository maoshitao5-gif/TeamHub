"""个人设备的主动 AI 整理：本机准备、明确确认、只读建议。"""
import hashlib
import os
import secrets
import stat
import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock, Semaphore
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core import ai_settings
from backend.app.core.ai_client import AIError, complete, test_vision
from backend.app.core.storage_paths import document_path
from backend.app.database import get_db
from backend.app.models import Document, Tag
from backend.app.services.ai_text import AITextError, extract_text

MAX_FILE_BYTES = 10 * 1024 * 1024
PREPARATION_TTL = 600
_preparations = OrderedDict()
_cache_lock = RLock()
_paid_call = Semaphore(1)


def _error(status, code, message):
    return HTTPException(status, detail={'error': {'code': code, 'message': message, 'details': None}})


class SafeAIRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # 默认验证处理器会回传 body；密钥相关路由只能返回固定消息。
                raise _error(422, 'AI_INVALID_INPUT', 'AI 请求格式不正确，请检查填写内容') from None
            except AIError as exc:
                raise _error(exc.status, exc.code, str(exc)) from None
            except ai_settings.AISettingsError as exc:
                raise _error(400, 'AI_SETTINGS_ERROR', str(exc)) from None
            except OSError:
                raise _error(503, 'AI_LOCAL_IO_ERROR', 'AI 本机配置或资料读取失败，请检查后重试') from None
        return safe


def _local_request(request: Request):
    local = {'localhost', '127.0.0.1', '::1'}
    peer = request.client.host if request.client else ''
    if peer == 'testclient':
        local.add('testserver')
    origin = request.headers.get('origin')
    if (request.url.hostname not in local or peer not in {'127.0.0.1', '::1', 'testclient'}
            or request.headers.get('x-teamhub-ai') != '1'
            or (origin is not None and origin not in settings.allowed_origins)):
        raise _error(403, 'AI_LOCAL_REQUEST_REQUIRED', 'AI 功能仅允许从本机 TeamHub 界面调用')
    if settings.is_electron:
        capability = os.getenv('TEAMHUB_AI_CAPABILITY', '')
        supplied = request.headers.get('x-teamhub-ai-capability', '')
        # null Origin 也可来自网页的 sandbox iframe，不能当作桌面身份。
        if not capability or not secrets.compare_digest(capability.encode(), supplied.encode()):
            raise _error(403, 'AI_DESKTOP_SESSION_REQUIRED', '请从当前 TeamHub 桌面使用 AI 功能；更新后请重新启动桌面')


router = APIRouter(prefix='/api/ai', tags=['AI辅助整理'], route_class=SafeAIRoute,
                   dependencies=[Depends(_local_request)])


class AISettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    base_url: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*$')
    api_key: SecretStr | None = Field(None, max_length=4096)
    clear_api_key: StrictBool = False


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    preparation_id: str = Field(min_length=1, max_length=100)
    consent: StrictBool = False


class AITestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['connection', 'vision'] = 'connection'


@router.get('/settings')
def ai_status() -> dict:
    return ai_settings.get_settings()


@router.put('/settings')
def configure_ai(body: AISettingsUpdate) -> dict:
    return ai_settings.update_settings(body.base_url, body.model,
        api_key=body.api_key.get_secret_value() if body.api_key is not None else None,
        clear_api_key=body.clear_api_key)


@router.post('/test')
def test_connection(body: AITestRequest | None = None) -> dict:
    if not _paid_call.acquire(blocking=False):
        raise _error(429, 'AI_BUSY', '已有 AI 请求正在处理，请稍候')
    try:
        config, key, revision = ai_settings.get_connection()
        if body is not None and body.mode == 'vision':
            matched = test_vision(config, key)
            if ai_settings.get_connection()[2] != revision:
                raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 设置已更改，原测试结果已丢弃，请重新确认')
            return {'ok': matched, 'mode': 'vision', 'message': (
                '本次测试成功识别合成图中的颜色和形状。复杂图纸和资料仍需结合实际内容核对。' if matched else
                '本次未能正确识别合成测试图。请核对所选模型的图像理解能力；单次结果不代表其全部能力。')}
        complete(config, key)
        if ai_settings.get_connection()[2] != revision:
            raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 设置已更改，原测试结果已丢弃，请重新确认')
        return {'ok': True, 'message': '连接成功，AI 服务已正常响应'}
    finally:
        _paid_call.release()


def _scope():
    if not settings.library_path:
        raise _error(400, 'AI_LIBRARY_REQUIRED', '请先打开个人文件库')
    return str(Path(settings.library_path).resolve())


def _signature(config):
    return (config['base_url'], config['model'])


def _read(doc):
    if doc is None or doc.status not in {'pending', 'organized'} or doc.is_folder:
        raise _error(409, 'AI_DOCUMENT_CHANGED', '资料已变化或不支持此类型，请重新选择资料')
    path = document_path(doc)
    # 不接受任意用户路径；只读已登记资料，且阻止最终文件为符号链接。
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise _error(422, 'AI_FILE_TOO_LARGE', '第一期 AI 辅助整理支持不超过 10 MiB 的单个文件')
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise _error(422, 'AI_FILE_TOO_LARGE', '文件内容过大，请选择较小资料')
    except OSError:
        raise _error(409, 'AI_DOCUMENT_CHANGED', '资料无法读取或已变化，请重新选择资料') from None
    identity = (doc.name, doc.storage_path, doc.storage_area, doc.original_path,
                doc.current_version_id, doc.status, hashlib.sha256(data).hexdigest())
    return path, data, identity


def _check(entry, db):
    if entry['scope'] != _scope():
        raise _error(409, 'AI_PREPARATION_EXPIRED', '个人库已切换，请重新准备并确认发送内容')
    db.expire_all()
    doc = db.query(Document).filter(Document.id == entry['document_id']).first()
    try:
        _, _, identity = _read(doc)
    except HTTPException:
        raise _error(409, 'AI_DOCUMENT_CHANGED', '资料已变化，请重新准备并确认发送内容') from None
    if identity != entry['identity']:
        raise _error(409, 'AI_DOCUMENT_CHANGED', '资料已变化，请重新准备并确认发送内容')


@router.post('/documents/{document_id}/prepare')
def prepare_document(document_id: str, db: Session = Depends(get_db)) -> dict:
    scope = _scope()
    doc = db.query(Document).filter(Document.id == document_id).first()
    if doc is None:
        raise _error(404, 'AI_DOCUMENT_NOT_FOUND', '资料不存在')
    path, data, identity = _read(doc)
    try:
        extracted = extract_text(path, path.name, data=data)
    except AITextError as exc:
        raise _error(422, 'AI_UNSUPPORTED_CONTENT', str(exc)) from None
    config = ai_settings.get_settings()
    public = {
        'preparation_id': secrets.token_urlsafe(32), 'document_id': doc.id,
        'document_name': doc.name, 'content_hash': identity[-1],
        'text': extracted['text'], 'truncated': extracted['truncated'],
        'notes': ['分析对象是当前工作文件，可能包含尚未保存为版本的修改。', *extracted['notes']],
        'expires_in': PREPARATION_TTL,
        'service': {'base_url': config['base_url'], 'model': config['model']},
    }
    entry = {**public, 'scope': scope, 'identity': identity,
             'config': _signature(config), 'created': time.monotonic(), 'result': None}
    with _cache_lock:
        now = time.monotonic()
        for key in list(_preparations):
            if now - _preparations[key]['created'] >= PREPARATION_TTL:
                _preparations.pop(key)
        while len(_preparations) >= 16:
            _preparations.popitem(last=False)
        _preparations[public['preparation_id']] = entry
    return public


@router.post('/analyze')
def analyze_document(body: AnalyzeRequest, db: Session = Depends(get_db)) -> dict:
    if body.consent is not True:
        raise _error(400, 'AI_CONSENT_REQUIRED', '请先确认允许将所选内容发送给 AI 服务')
    with _cache_lock:
        entry = _preparations.get(body.preparation_id)
        if entry is None or time.monotonic() - entry['created'] >= PREPARATION_TTL:
            _preparations.pop(body.preparation_id, None)
            raise _error(409, 'AI_PREPARATION_EXPIRED', '待发送内容已过期，请重新准备并确认')
    _check(entry, db)
    config, key, revision = ai_settings.get_connection()
    if _signature(config) != entry['config']:
        raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 服务设置已更改，请重新准备并确认发送内容')
    if entry['result'] is not None:
        return entry['result']
    if not _paid_call.acquire(blocking=False):
        raise _error(429, 'AI_BUSY', '已有 AI 请求正在处理，请稍候')
    try:
        with _cache_lock:
            if (_preparations.get(body.preparation_id) is not entry
                    or time.monotonic() - entry['created'] >= PREPARATION_TTL):
                raise _error(409, 'AI_PREPARATION_EXPIRED', '本次任务已结束或过期，请重新准备并确认')
        # 排队间隙也可能刚刚完成同一个任务；成功结果可重取，不能再次收费。
        if entry['result'] is not None:
            return entry['result']
        generated = complete(config, key, filename=entry['document_name'], text=entry['text'])
        _check(entry, db)
        if ai_settings.get_connection()[2] != revision:
            raise _error(409, 'AI_CONFIGURATION_CHANGED', 'AI 设置已更改，原请求结果已丢弃，请重新确认')
        # 已有标签只在本机匹配；不会把全库标签词表发送给服务商。
        names = {name.casefold(): name for name, in db.query(Tag.name).limit(5000)}
        tags = list(dict.fromkeys(names.get(tag.casefold(), tag) for tag in generated['tags']))
        result = {
            'preparation_id': body.preparation_id, 'document_id': entry['document_id'],
            'document_name': entry['document_name'], 'content_hash': entry['content_hash'],
            'summary': generated['summary'], 'tags': tags, 'model': config['model'],
            'truncated': entry['truncated'], 'notes': entry['notes'],
        }
        with _cache_lock:
            entry['result'] = result
        return result
    except (AIError, HTTPException, ai_settings.AISettingsError):
        # 失败可能已经计费；禁止后台自动重试，重新准备后由用户决定。
        with _cache_lock:
            _preparations.pop(body.preparation_id, None)
        raise
    finally:
        _paid_call.release()
