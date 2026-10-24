"""
共享适配层(M4,契约 §5.1/§5.2;蓝图 §3.6)

本地服务是唯一共享出入口:发布/提交/取回全部经此转发到共享服务,
前端不直连共享后端(契约 §7,V-13)。

纪律(蓝图 §3.5、M4-5):
- 上传/下载流式转发,不把大文件读进内存;
- 只有收到共享服务成功响应(或按 request_id 确认成功)后才落 share_links,
  首次成功前本地不记录"已共享"状态;
- 再次提交必须显式选择"更新原资料"(submit-version,需已有 share_link)
  或"另建资料"(publish + force_new),不按文件名猜测归属。
"""
import uuid
import hashlib
import json
import os
import shutil
import tempfile
import threading
from functools import wraps
from pathlib import Path
from typing import Optional, Literal, Callable
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.types import Scope, Receive, Send
from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, object_session

from backend.app.config import settings
from backend.app.api.sync import _latest_session_config, _session_lock
from backend.app.database import get_db, load_library_config, write_json_config
from backend.app.models import Document, Version, ShareLink, RemoteOperation, utc_now
from backend.app.core.cloud_client import (get_cloud_url, auth_headers, refresh_token, cloud_req,
                                          verify_server_identity, CloudIdentityError, _http,
                                          _validate_request_target)
from backend.app.core.file_utils import get_document_or_404
from backend.app.core.logger import get_logger
from backend.app.services import versions as version_service
from backend.app.services.versions import VersionServiceError
from backend.app.services import remote_update, snapshots, content_duplicates

logger = get_logger("api.sharing")
router = APIRouter(prefix="/api/sharing", tags=["sharing"])


def register_cloud_error_handler(app) -> None:
    """把 _CloudError 转成与共享服务一致的状态码 + 错误信封(契约 §4)"""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(_CloudError)
    async def _handle(request: Request, exc: "_CloudError"):
        return JSONResponse(status_code=exc.status_code, content=exc.payload)


# ========== 请求 DTO ==========

class PublishRequest(BaseModel):
    """发布本地资料到共享空间"""
    doc_id: str
    workspace_id: Optional[str] = None       # 缺省读 config.active_workspace_id
    request_id: Optional[str] = Field(None, min_length=1, max_length=128)         # 缺省服务端生成并返回
    note: Optional[str] = None
    confirmed: bool = False
    force_new: bool = False                  # 已有关联时另建共享资料并重指关联
    visibility: Literal['workspace', 'restricted']
    access: list[dict] = Field(default_factory=list)


class SubmitVersionRequest(BaseModel):
    """向已关联的共享资料提交新版本(更新原资料)"""
    doc_id: str
    confirmed: bool = False
    request_id: Optional[str] = Field(None, min_length=1, max_length=128)
    note: Optional[str] = None
    confirm_old_content: bool = False
    expected_workspace_id: Optional[str] = None
    expected_document_id: Optional[str] = None


class PullRequest(BaseModel):
    """取回共享资料指定版本到本地库新位置"""
    cloud_doc_id: str
    workspace_id: Optional[str] = None
    version_id: Optional[str] = None         # 缺省取 current_version_id 指定版本
    target_dir: Optional[str] = None         # 相对文件库根,缺省根目录
    name: Optional[str] = None               # 缺省用共享资料名
    tags: list[str] = Field(default_factory=lambda: ["共享"])


class UpdateLocalRequest(BaseModel):
    doc_id: str
    version_id: Optional[str] = None
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=128)
    confirmed: bool = False


class ConfirmBaseRequest(BaseModel):
    version_id: str
    confirmed: bool = False


class SharedTagUpdateRequest(BaseModel):
    """增删共享资料的标签关联(M5;add=标签名,remove=关联 tag_id)"""
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    workspace_id: Optional[str] = None


# ========== 内部辅助 ==========

class _CloudError(Exception):
    """共享服务错误,透传状态码与错误信封"""
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload))


def _cloud_context(workspace_id: Optional[str]) -> tuple[dict, str, str]:
    """校验登录与空间选择,返回 (config, cloud_url, workspace_id)"""
    config = load_library_config()
    if not config.get("cloud_access_token"):
        raise HTTPException(401, "尚未登录共享账号,请先登录")
    ws = workspace_id or config.get("active_workspace_id")
    if not ws:
        raise HTTPException(400, "请选择共享资料库")
    return config, get_cloud_url(config), ws


def _passthrough(resp: httpx.Response):
    """非 2xx 响应按契约信封透传;无信封时包装为 STORAGE_ERROR"""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and "error" in body:
        raise _CloudError(resp.status_code, body)
    if isinstance(body, dict) and "detail" in body:
        raise _CloudError(resp.status_code, {
            "error": {"code": "UPSTREAM_ERROR", "message": str(body["detail"]), "details": None}
        })
    raise _CloudError(resp.status_code, {
        "error": {"code": "STORAGE_ERROR", "message": f"共享服务响应异常({resp.status_code})", "details": None}
    })


def _ensure_fresh_token(config: dict) -> None:
    """上传前探活:GET /users/me 触发一次自动续期,避免流式上传中途 401 无法重试"""
    cloud_url = get_cloud_url(config)
    resp = cloud_req("GET", f"{cloud_url}/api/users/me", config, timeout=15)
    if resp.status_code != 200:
        _passthrough(resp)
    return resp.json()


def _verified_identity(config: dict, link: ShareLink | None = None,
                       expected_server_id: str | None = None) -> tuple[str, str]:
    with _session_lock:
        config_path = Path(settings.config_file)
        _latest_session_config(config, config_path)
        if link:
            session = object_session(link)
            source_database = session.get_bind(mapper=ShareLink).url.database if session else None
            current_database = make_url(settings.database_url).database
            if (not source_database or not current_database
                    or Path(source_database).resolve() != Path(current_database).resolve()):
                raise HTTPException(409, '共享关联不属于当前个人库，请重新打开资料')
    try:
        server_id = verify_server_identity(config, link.server_id if link else expected_server_id)
    except httpx.HTTPError as exc:
        if link and link.server_url.rstrip('/') != get_cloud_url(config):
            raise HTTPException(409, '新地址无法核实为原共享服务，未发送凭据或资料') from exc
        raise
    except (CloudIdentityError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if link and not link.server_id and link.server_url.rstrip('/') != get_cloud_url(config):
        raise HTTPException(409, '旧关联须先连接原地址核实服务身份')
    actor = _ensure_fresh_token(config)['id']
    # 身份确认后再次校验原个人库；晚到请求不能绑定旧关联或转写另一库。
    with _session_lock:
        latest = _latest_session_config(config, config_path)
        if link:
            try:
                remote_update.bind_link(link, server_id=server_id, account_id=actor,
                                        server_url=get_cloud_url(config))
            except VersionServiceError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
        latest['cloud_server_id'] = server_id
        write_json_config(config_path, latest)
    config['cloud_server_id'] = server_id
    return server_id, actor


def _upload_object(config: dict, cloud_url: str, ws_id: str,
                   object_path: Path, filename: str) -> dict:
    """把本地不可变快照对象流式上传到共享服务暂存,返回 {upload_id, sha256, file_size}"""
    _ensure_fresh_token(config)
    headers = {"Authorization": auth_headers(config)["Authorization"],
               "Content-Type": "application/octet-stream"}
    with open(object_path, "rb") as f:
        resp = _http.post(
            f"{cloud_url}/api/workspaces/{ws_id}/uploads",
            params={"filename": filename},
            content=f,           # httpx 文件对象流式发送
            headers=headers,
            timeout=httpx.Timeout(30.0, read=600.0),
        )
    if resp.status_code != 200:
        _passthrough(resp)
    return resp.json()


def _current_version(db: Session, doc: Document) -> Version:
    version = db.get(Version, doc.current_version_id) if doc.current_version_id else None
    if not version:
        raise HTTPException(400, "文档没有可用版本,无法共享")
    return version


def _object_path_of(version: Version) -> Path:
    path = Path(version.file_path)
    if not path.is_file():
        raise HTTPException(500, f"版本快照缺失: v{version.version_number}")
    return path


# 本地桌面服务只有一个写入进程；避免同请求并发创建不同日志/关联。
_sharing_lock = threading.RLock()


def _serialized(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        with _sharing_lock:
            try:
                return operation(*args, **kwargs)
            except httpx.HTTPError as exc:
                raise HTTPException(502, "共享服务不可达，请保留请求编号重试") from exc
            except VersionServiceError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
            except CloudIdentityError as exc:
                raise HTTPException(409, str(exc)) from exc
            except content_duplicates.DuplicateCheckError as exc:
                raise HTTPException(409, str(exc)) from exc
    return run


def _is_terminal_duplicate(result: dict) -> bool:
    error = result.get('error')
    return (result.get('status') == 'failed'
            and (result.get('code') == 'DUPLICATE_CONTENT'
                 or isinstance(error, dict) and error.get('code') == 'DUPLICATE_CONTENT'))


def _publication_failure(result: dict, request_id: str, ws_id: str) -> _CloudError:
    error = result.get('error')
    if not isinstance(error, dict):
        error = {'code': 'PUBLICATION_FAILED', 'message': '发布未成功'}
    return _CloudError(409, {'error': {**error,
        'details': {'request_id': request_id, 'workspace_id': ws_id,
                    'document_id': result.get('document_id'),
                    'duplicate_document': result.get('duplicate_document')}}})


def _send_publication(req, db, action):
    if not req.confirmed:
        raise HTTPException(400, "请先确认共享范围与随行标签")
    doc = get_document_or_404(db, req.doc_id)
    if doc.status == "trashed":
        raise HTTPException(400, "资料在回收站中")
    link = db.query(ShareLink).filter(ShareLink.local_document_id == doc.id).first()
    if action == "submit_version" and not link:
        raise HTTPException(400, "该资料尚未关联共享资料，请先发布")
    if action == 'submit_version' and (
            (req.expected_workspace_id and req.expected_workspace_id != link.workspace_id)
            or (req.expected_document_id and req.expected_document_id != link.shared_document_id)):
        raise HTTPException(409, '原共享关联已变化，请关闭后重新确认原资料位置')
    config, cloud_url, ws_id = _cloud_context(link.workspace_id if action == "submit_version" else req.workspace_id)
    server_id, actor = _verified_identity(config, link if action == 'submit_version' else None)
    request_id = req.request_id or str(uuid.uuid4())
    identity = dict(server=server_id, workspace=ws_id, actor=actor, request_id=request_id)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    journal_path = Path(settings.library_path) / '.teamhub' / 'publications' / (key + '.json')
    intent = dict(action=action, doc_id=doc.id, note=req.note, force_new=getattr(req, 'force_new', False),
                  visibility=getattr(req, 'visibility', None), access=getattr(req, 'access', None))
    frozen_operation = db.query(RemoteOperation).filter_by(server_id=server_id, account_id=actor,
        workspace_id=ws_id, request_id=request_id).first()
    if not journal_path.exists() and not frozen_operation:
        legacy_identity = {**identity, 'server': cloud_url}
        legacy_key = hashlib.sha256(json.dumps(legacy_identity, sort_keys=True).encode()).hexdigest()
        legacy_path = journal_path.parent / (legacy_key + '.json')
        if legacy_path.exists():
            legacy = json.loads(legacy_path.read_text())
            if (legacy.get('identity') != legacy_identity
                    or any(intent.get(k) != value for k, value in legacy['intent'].items())
                    or getattr(req, 'visibility', 'workspace') != 'workspace'
                    or getattr(req, 'access', [])):
                raise HTTPException(409, '旧发布请求与当前操作不一致')
            legacy.update(identity=identity, intent=intent)
            write_json_config(journal_path, legacy)
    if frozen_operation and not journal_path.exists():
        frozen = json.loads(frozen_operation.input_json)
        if frozen_operation.kind != action or frozen.get('intent') != intent:
            raise HTTPException(409, '请求编号与已冻结发布不一致')
        journal = {**frozen, 'identity': identity,
                   'filename': frozen['body']['original_filename']}
        if frozen_operation.status == 'succeeded':
            journal.update(applied=True, result=json.loads(frozen_operation.result_json))
        write_json_config(journal_path, journal)
    if journal_path.exists():
        journal = json.loads(journal_path.read_text())
        if journal['intent'] != intent:
            raise HTTPException(409, "同一请求编号不能用于不同操作")
        if journal.get('applied'):
            return journal['result']
    else:
        if action == "publish" and link and not req.force_new:
            raise HTTPException(400, "资料已有共享关联，请选择更新原资料或另建资料")
        if action == "submit_version":
            selected = _current_version(db, doc)
            if (selected.version_number < doc.version_sequence
                    and remote_update.content_hash(version_service.working_path(doc), doc.is_folder) == selected.sha256_hash
                    and not req.confirm_old_content):
                raise _CloudError(409, {'error': {'code': 'OLD_CONTENT_CONFIRMATION_REQUIRED',
                    'message': '将旧内容作为新的共享当前版本提交，请明确确认', 'details': None}})
            try:
                version, _ = version_service.snapshot_working_copy(db, doc, note=req.note)
            except VersionServiceError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
            db.commit()
        else:
            # Capture actual working bytes without making a Version first. A
            # cloud duplicate must not create an otherwise unnecessary version.
            source = version_service.working_path(doc)
            library = Path(settings.library_path)
            try:
                if doc.is_folder:
                    prepared = snapshots.store_folder_snapshot(library, source)
                else:
                    key, digest, size = snapshots.store_file_snapshot(library, source)
                    prepared = (key, digest, None, size)
            except snapshots.SnapshotError as exc:
                raise HTTPException(409, str(exc)) from exc
            duplicate = cloud_req('POST', f'{cloud_url}/api/workspaces/{ws_id}/duplicates', config,
                                  json={'doc_type': doc.doc_type, 'content_hash': prepared[1]})
            if duplicate.status_code != 200:
                _passthrough(duplicate)
            candidates = duplicate.json()
            if not isinstance(candidates, list):
                raise HTTPException(502, '共享查重响应异常，未发布资料')
            if candidates:
                candidate = candidates[0]
                if not isinstance(candidate, dict) or not candidate.get('document_id'):
                    raise HTTPException(502, '共享查重响应异常，未发布资料')
                brief = {'document_id': candidate['document_id'], 'name': candidate.get('name'),
                         'doc_type': candidate.get('doc_type'),
                         'current_version_id': candidate.get('current_version_id'),
                         'current_version_number': candidate.get('current_version_number'),
                         'total_size': candidate.get('total_size')}
                raise _CloudError(409, {'error': {'code': 'DUPLICATE_CONTENT',
                    'message': '云端已存在相同文件，未重复新增资料',
                    'details': {'request_id': request_id, 'workspace_id': ws_id,
                                'document_id': brief['document_id'], 'duplicate_document': brief}}})
            version, _ = version_service.snapshot_working_copy(db, doc, note=req.note,
                                                             prepared_snapshot=prepared)
            db.commit()
        obj = _object_path_of(version)
        body = dict(request_id=request_id, confirmed=True, content_hash=version.sha256_hash,
                    manifest_hash=version.manifest_hash, original_filename=version.original_filename, note=req.note)
        if action == "publish":
            endpoint = f"/api/workspaces/{ws_id}/publications"
            body.update(name=doc.name, description=doc.description, doc_type=doc.doc_type,
                        tags=[t.name for t in doc.tags], force_new=req.force_new,
                        visibility=req.visibility, access=req.access)
        else:
            endpoint = f"/api/workspaces/{ws_id}/documents/{link.shared_document_id}/versions"
            body['base_version_id'] = link.base_version_id
        journal = dict(intent=intent, identity=identity, body=body, endpoint=endpoint,
                       object_path=str(obj), filename=version.original_filename, local_version_id=version.id)
        write_json_config(journal_path, journal)
    operation = remote_update.freeze_operation(db, server_id=server_id, account_id=actor,
        workspace_id=ws_id, request_id=request_id, kind=action, local_document_id=doc.id,
        payload={'intent': journal['intent'], 'body': journal['body'],
                 'local_version_id': journal.get('local_version_id'),
                 'object_path': journal['object_path'], 'endpoint': journal['endpoint']})
    stored_result = json.loads(operation.result_json) if operation.result_json else {}
    terminal_duplicate = (journal.get('terminal_failed')
                          or operation.status == 'failed' and _is_terminal_duplicate(stored_result))
    try:
        if terminal_duplicate:
            # A definite duplicate is terminal. Recheck the recorded result's
            # current cloud ACL; never resend its file or expose cached names.
            resp = cloud_req('GET', f'{cloud_url}/api/workspaces/{ws_id}/publications/{quote(request_id, safe="")}',
                             config, timeout=30)
            if resp.status_code != 200:
                _passthrough(resp)
            result = resp.json()
            if not isinstance(result, dict) or not _is_terminal_duplicate(result):
                raise HTTPException(409, '远端查重失败记录的状态异常，未重新发送资料')
        else:
            # Every uncertain retry reuses its frozen request and object, even
            # if the user has subsequently edited the working copy.
            upload = _upload_object(config, cloud_url, ws_id, Path(journal['object_path']), journal['filename'])
            body = {**journal['body'], 'upload_id': upload['upload_id']}
            resp = cloud_req('POST', cloud_url + journal['endpoint'], config, json=body, timeout=600)
            if resp.status_code != 200:
                _passthrough(resp)
            result = resp.json()
    except httpx.HTTPError as exc:
        # 服务端可能已经成功；同编号重试返回原结果，不猜测成功或失败。
        raise _CloudError(502, {'error': {'code': 'RETRY_REQUIRED',
            'message': '共享结果尚未确认，请使用同一请求编号重试',
            'details': {'request_id': request_id}}}) from exc
    if _is_terminal_duplicate(result):
        operation.status = 'failed'
        operation.error = '云端已存在相同文件，未重复新增资料'
        operation.result_json = json.dumps(result, ensure_ascii=False)
        db.commit()
        journal.update(result=result, terminal_failed=True)
        write_json_config(journal_path, journal)
        raise _publication_failure(result, request_id, ws_id)
    if result.get('status') != 'succeeded' or not result.get('document_id') or not result.get('version_id'):
        raise _publication_failure(result, request_id, ws_id)
    # 已确认结果写日志，进程在本地数据库提交前中断也可通过原编号恢复关联。
    journal['result'] = result
    write_json_config(journal_path, journal)
    if not link:
        link = ShareLink(local_document_id=doc.id)
        db.add(link)
    link.server_url, link.workspace_id = cloud_url, ws_id
    link.server_id, link.account_id = server_id, actor
    link.shared_document_id = result['document_id']
    link.base_version_id = result['version_id']
    link.observed_version_id = result['version_id']
    link.updated_at = utc_now()
    doc.cloud_doc_id = result['document_id']
    version = db.get(Version, journal.get('local_version_id'))
    if version:
        remote_update.record_source(db, doc, version, server_id=server_id, account_id=actor,
            workspace_id=ws_id, source_type='shared', shared_document_id=result['document_id'],
            shared_version_id=result['version_id'], operation_id=operation.id)
    operation.status, operation.result_json = 'succeeded', json.dumps(result)
    db.commit()
    journal['applied'] = True
    write_json_config(journal_path, journal)
    return result


@router.post('/publish')
@_serialized
def publish(req: PublishRequest, db: Session = Depends(get_db)):
    return _send_publication(req, db, 'publish')


@router.post('/submit-version')
@_serialized
def submit_version(req: SubmitVersionRequest, db: Session = Depends(get_db)):
    return _send_publication(req, db, 'submit_version')


def _stream_to_file(resp, target, expected_hash, expected_size):
    if resp.status_code != 200:
        resp.read()
        _passthrough(resp)
    digest, size = hashlib.sha256(), 0
    with target.open('xb') as output:
        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
            size += len(chunk)
            if size > expected_size:
                raise HTTPException(422, '下载内容超过声明大小')
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if size != expected_size or digest.hexdigest() != expected_hash:
        raise HTTPException(422, '下载内容摘要或大小不符，未取回资料')


@router.post('/pull')
@_serialized
def pull(req: PullRequest, db: Session = Depends(get_db)):
    """下载到私有暂存并校验，再还原新工作位置；失败回滚自建文件与数据库。"""
    from backend.app.services import snapshots
    from backend.app.api.documents import _get_or_create_tags
    if not settings.library_path:
        raise HTTPException(400, '文件库未初始化')
    config, cloud_url, ws = _cloud_context(req.workspace_id)
    server_id, actor = _verified_identity(config)
    library = Path(settings.library_path).resolve()
    target_dir = (library / (req.target_dir or '')).resolve()
    if not target_dir.is_relative_to(library) or target_dir.is_relative_to(library / '.teamhub'):
        raise HTTPException(400, '取回位置必须位于文件库工作目录内')
    detail = cloud_req('GET', f'{cloud_url}/api/workspaces/{ws}/documents/{req.cloud_doc_id}', config)
    if detail.status_code != 200:
        _passthrough(detail)
    remote = detail.json()
    response = cloud_req('GET', f'{cloud_url}/api/workspaces/{ws}/documents/{req.cloud_doc_id}/versions', config)
    if response.status_code != 200:
        _passthrough(response)
    chosen_id = req.version_id or remote.get('current_version_id')
    chosen = next((v for v in response.json() if v['id'] == chosen_id), None)
    if not chosen:
        raise HTTPException(404, '指定或当前共享版本不存在')
    filename = chosen['original_filename']
    if (not filename or filename in {'.', '..'} or '/' in filename or '\\' in filename or ':' in filename):
        raise HTTPException(422, '共享文件名不安全')
    folder = remote.get('doc_type') == 'folder'
    expected_hash = chosen.get('manifest_hash') if folder else chosen.get('content_hash')
    if (not expected_hash or isinstance(chosen.get('file_size'), bool)
            or not isinstance(chosen.get('file_size'), int) or chosen['file_size'] < 0
            or chosen['file_size'] > settings.shared_download_max_bytes):
        raise HTTPException(422, '共享版本缺少可靠摘要或超过下载大小上限，无法取回')
    duplicate = content_duplicates.find_current_duplicate(
        db, content_hash=chosen.get('content_hash'), file_size=chosen['file_size'], is_folder=folder)
    if duplicate:
        return content_duplicates.duplicate_result(duplicate)
    staging = library / '.teamhub' / 'downloads'
    staging.mkdir(parents=True, exist_ok=True)
    final_path = None
    with tempfile.TemporaryDirectory(dir=staging) as tmp:
        archive = Path(tmp) / 'content'
        url = f"{cloud_url}/api/workspaces/{ws}/documents/{req.cloud_doc_id}/versions/{chosen['id']}/download"
        _ensure_fresh_token(config)
        try:
            for attempt in range(2):
                with _http.stream('GET', url, headers=auth_headers(config), timeout=httpx.Timeout(30, read=600)) as resp:
                    if resp.status_code == 401 and attempt == 0 and refresh_token(config):
                        continue
                    _stream_to_file(resp, archive, expected_hash, chosen['file_size'])
                    break
            if folder:
                unpacked = Path(tmp) / 'folder'
                unpacked.mkdir()
                snapshots._extract_zip_safely(archive, unpacked)
                prepared = snapshots.store_folder_snapshot(library, unpacked)
                if prepared[1] != chosen['content_hash']:
                    raise HTTPException(422, '文件夹清单摘要不符')
            else:
                key, digest, size = snapshots.store_file_snapshot(library, archive)
                prepared = (key, digest, None, size)
            # export_object 使用排他创建；即使同名并发出现也不覆盖。
            candidate, number = filename, 1
            while (target_dir / candidate).exists() or (target_dir / candidate).is_symlink():
                name = Path(filename)
                candidate = f'{name.stem}_{number}{name.suffix}'
                number += 1
            final_path = snapshots.export_object(library, prepared[0], candidate, target_dir, folder)
            doc = Document(name=req.name or remote['name'], description=remote.get('description'),
                           is_folder=folder, doc_type=remote['doc_type'], storage_path=str(final_path.relative_to(library)),
                           storage_mode='copy', original_path=f'cloud://{ws}/{req.cloud_doc_id}', status='organized',
                           cloud_source='cloud_import', cloud_doc_id=req.cloud_doc_id)
            db.add(doc)
            db.flush()
            doc.tags = _get_or_create_tags(db, req.tags or ['共享'])
            version, _ = version_service.snapshot_working_copy(db, doc, prepared_snapshot=prepared,
                                                 note=f"取回自共享版本 v{chosen['version_number']}")
            db.add(ShareLink(local_document_id=doc.id, server_url=cloud_url, workspace_id=ws,
                             server_id=server_id, account_id=actor, observed_version_id=chosen['id'],
                             shared_document_id=req.cloud_doc_id, base_version_id=chosen['id']))
            remote_update.record_source(db, doc, version, server_id=server_id, account_id=actor,
                workspace_id=ws, source_type='shared', shared_document_id=req.cloud_doc_id,
                shared_version_id=chosen['id'])
            db.commit()
            return dict(document_id=doc.id, name=doc.name, storage_path=doc.storage_path,
                        base_version_id=chosen['id'], version_number=chosen['version_number'])
        except Exception as exc:
            db.rollback()
            if final_path is not None:
                shutil.rmtree(final_path) if folder else final_path.unlink(missing_ok=True)
            if isinstance(exc, (snapshots.SnapshotError, VersionServiceError, OSError)):
                raise HTTPException(422, str(exc)) from exc
            raise


# ========== 关联查询 ==========

def _remote_versions(config: dict, cloud_url: str, ws: str, shared_id: str) -> tuple[dict, list]:
    detail = cloud_req('GET', f'{cloud_url}/api/workspaces/{ws}/documents/{shared_id}', config)
    if detail.status_code != 200:
        _passthrough(detail)
    response = cloud_req('GET', f'{cloud_url}/api/workspaces/{ws}/documents/{shared_id}/versions', config)
    if response.status_code != 200:
        _passthrough(response)
    return detail.json(), response.json()


def _download_fixed(config: dict, cloud_url: str, ws: str, shared_id: str,
                    chosen: dict, folder: bool) -> tuple:
    from backend.app.services import snapshots
    expected = chosen.get('manifest_hash') if folder else chosen.get('content_hash')
    size = chosen.get('file_size')
    if not expected or not isinstance(size, int) or not 0 <= size <= settings.shared_download_max_bytes:
        raise HTTPException(422, '目标版本摘要无效或超过下载大小上限')
    staging = Path(settings.library_path) / '.teamhub' / 'downloads'
    staging.mkdir(parents=True, exist_ok=True)
    url = f"{cloud_url}/api/workspaces/{ws}/documents/{shared_id}/versions/{chosen['id']}/download"
    with tempfile.TemporaryDirectory(dir=staging) as tmp:
        archive = Path(tmp) / 'content'
        for attempt in range(2):
            with _http.stream('GET', url, headers=auth_headers(config), timeout=httpx.Timeout(30, read=600)) as response:
                if response.status_code == 401 and attempt == 0 and refresh_token(config):
                    continue
                _stream_to_file(response, archive, expected, size)
                break
        if folder:
            unpacked = Path(tmp) / 'folder'
            unpacked.mkdir()
            snapshots._extract_zip_safely(archive, unpacked)
            prepared = snapshots.store_folder_snapshot(Path(settings.library_path), unpacked)
            if prepared[1] != chosen['content_hash']:
                raise HTTPException(422, '文件夹目标内容摘要不符')
            return prepared
        key, digest, file_size = snapshots.store_file_snapshot(Path(settings.library_path), archive)
        return key, digest, None, file_size


@router.post('/update-local')
@_serialized
def update_local(req: UpdateLocalRequest, db: Session = Depends(get_db)):
    if not req.confirmed:
        raise HTTPException(400, '请确认采用云端目标内容，本地修改将自动保护')
    doc = get_document_or_404(db, req.doc_id)
    link = db.query(ShareLink).filter_by(local_document_id=doc.id).first()
    if not link:
        raise HTTPException(409, '该资料没有可更新的共享关联')
    config, cloud_url, ws = _cloud_context(link.workspace_id)
    server_id, actor = _verified_identity(config, link)
    remote, history = _remote_versions(config, cloud_url, ws, link.shared_document_id)
    if remote.get('doc_type') != doc.doc_type:
        raise HTTPException(409, '共享资料类型与本地资料不一致')
    link.observed_version_id = remote.get('current_version_id')
    old = db.query(RemoteOperation).filter_by(server_id=server_id, account_id=actor,
        workspace_id=ws, request_id=req.request_id).first()
    request = {'doc_id': doc.id, 'requested_version_id': req.version_id}
    if old:
        frozen = json.loads(old.input_json)
        if old.kind != 'update_local' or frozen.get('request') != request:
            raise HTTPException(409, '请求编号已用于另一份资料或不同目标')
        chosen_id = frozen['remote_version']['id']
    else:
        chosen_id = req.version_id or remote.get('current_version_id')
    chosen = next((item for item in history if item['id'] == chosen_id), None)
    if not chosen:
        raise HTTPException(404, '固定目标版本已不可访问，未改写本地')
    # A same-ID retry verifies remote access, then returns the original result
    # without downloading or touching later local edits.
    payload = {'request': request, 'remote_version': chosen, 'shared_document_id': link.shared_document_id}
    if old:
        payload = json.loads(old.input_json)
        if any(chosen.get(key) != payload['remote_version'].get(key)
               for key in ('id', 'content_hash', 'manifest_hash', 'file_size')):
            raise HTTPException(409, '远端固定版本内容发生变化，更新中止')
        chosen = payload['remote_version']
    operation = remote_update.freeze_operation(db, server_id=server_id, account_id=actor,
        workspace_id=ws, request_id=req.request_id, kind='update_local', payload=payload,
        local_document_id=doc.id)
    if operation.status == 'succeeded':
        return json.loads(operation.result_json)
    if operation.status == 'needs_review':
        raise HTTPException(409, operation.error or '上次更新待核对')
    prepared = _download_fixed(config, cloud_url, ws, link.shared_document_id, chosen, doc.is_folder)
    return remote_update.apply_received_snapshot(db, doc, link, operation, prepared, chosen)


@router.get('/links/{doc_id}/remote-status')
@_serialized
def remote_status(doc_id: str, db: Session = Depends(get_db)):
    get_document_or_404(db, doc_id)
    link = db.query(ShareLink).filter_by(local_document_id=doc_id).first()
    if not link:
        raise HTTPException(404, '资料没有共享关联')
    config, cloud_url, ws = _cloud_context(link.workspace_id)
    _verified_identity(config, link)
    remote, history = _remote_versions(config, cloud_url, ws, link.shared_document_id)
    link.observed_version_id = remote.get('current_version_id')
    db.commit()
    return dict(base_version_id=link.base_version_id, observed_version_id=link.observed_version_id,
                has_remote_update=link.base_version_id != link.observed_version_id, versions=history)


@router.post('/links/{doc_id}/confirm-base')
@_serialized
def confirm_base(doc_id: str, req: ConfirmBaseRequest, db: Session = Depends(get_db)):
    """Explicit conflict resolution, never invoked by observation or failed download."""
    from backend.app.core.changelog import record_change
    if not req.confirmed:
        raise HTTPException(400, '请明确确认以指定共享版本为基准提交已处理的内容')
    get_document_or_404(db, doc_id)
    link = db.query(ShareLink).filter_by(local_document_id=doc_id).first()
    if not link:
        raise HTTPException(404, '资料没有共享关联')
    config, cloud_url, ws = _cloud_context(link.workspace_id)
    _verified_identity(config, link)
    remote, _ = _remote_versions(config, cloud_url, ws, link.shared_document_id)
    if remote.get('current_version_id') != req.version_id:
        raise HTTPException(409, '云端已出现其他版本，请重新处理冲突')
    record_change(db, 'document', doc_id, 'confirm_shared_base',
                  {'from_version_id': link.base_version_id, 'to_version_id': req.version_id})
    link.base_version_id = req.version_id
    link.observed_version_id = req.version_id
    db.commit()
    return {'base_version_id': req.version_id}


@router.get('/operations/{request_id}')
@_serialized
def get_remote_operation(request_id: str, workspace_id: Optional[str] = None,
                         db: Session = Depends(get_db)):
    config, _, ws = _cloud_context(workspace_id)
    server_id, actor = _verified_identity(config)
    operation = db.query(RemoteOperation).filter_by(server_id=server_id, account_id=actor,
        workspace_id=ws, request_id=request_id).first()
    if not operation:
        raise HTTPException(404, '当前账号下未找到操作')
    return {'request_id': request_id, 'kind': operation.kind, 'status': operation.status,
            'result': json.loads(operation.result_json) if operation.result_json else None,
            'error': operation.error}

@router.get("/links/{doc_id}")
def get_link(doc_id: str, db: Session = Depends(get_db)):
    """查询本地资料的共享关联(无关联返回 linked=false,不报错)"""
    get_document_or_404(db, doc_id)
    link = db.query(ShareLink).filter(ShareLink.local_document_id == doc_id).first()
    if not link:
        return {"linked": False}
    return {
        "linked": True,
        "server_url": link.server_url,
        "server_id": link.server_id,
        "account_id": link.account_id,
        "workspace_id": link.workspace_id,
        "shared_document_id": link.shared_document_id,
        "base_version_id": link.base_version_id,
        "observed_version_id": link.observed_version_id,
        "updated_at": link.updated_at,
    }


@router.get('/links/{doc_id}/target')
def get_link_target(doc_id: str, db: Session = Depends(get_db)):
    """Resolve the original publication, independently of the browsing selection.

    Names are returned only after server/account identity and remote read access
    have been verified. The offline link inspection above deliberately stays local.
    """
    get_document_or_404(db, doc_id)
    link = db.query(ShareLink).filter_by(local_document_id=doc_id).first()
    if not link:
        raise HTTPException(404, '资料没有共享关联')
    config, cloud_url, ws_id = _cloud_context(link.workspace_id)
    try:
        _verified_identity(config, link)
        document_response = cloud_req('GET',
            f'{cloud_url}/api/workspaces/{quote(ws_id, safe="")}/documents/{quote(link.shared_document_id, safe="")}', config)
        if document_response.status_code != 200:
            _passthrough(document_response)
        document = document_response.json()
        workspace_response = cloud_req('GET', f'{cloud_url}/api/workspaces/{quote(ws_id, safe="")}', config)
        if workspace_response.status_code != 200:
            _passthrough(workspace_response)
        workspace = workspace_response.json()
        team_id = workspace['team_id']
        team_response = cloud_req('GET', f'{cloud_url}/api/teams/{quote(team_id, safe="")}', config)
        if team_response.status_code != 200:
            _passthrough(team_response)
        team = team_response.json()
        if (document.get('id') != link.shared_document_id or document.get('workspace_id') != ws_id
                or workspace.get('id') != ws_id or team.get('id') != team_id):
            raise HTTPException(502, '原共享资料信息不一致，请重试')
        target = dict(workspace_id=ws_id, workspace_name=workspace['name'],
            team_id=team_id, team_name=team['name'], shared_document_id=link.shared_document_id,
            shared_document_name=document['name'],
            can_contribute=bool(document.get('capabilities', {}).get('contribute')))
    except httpx.HTTPError as exc:
        raise HTTPException(502, '无法读取原共享资料，请检查云服务连接后重试') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, '原共享资料响应异常，请重试') from exc
    return JSONResponse(target, headers={'Cache-Control': 'no-store'})


# ========== 共享标签增删(前端不直连共享后端,V-13) ==========

@router.post("/tags/{shared_doc_id}")
def update_shared_tags(shared_doc_id: str, req: SharedTagUpdateRequest,
                       db: Session = Depends(get_db)):
    """代理共享标签增删;贡献者校验在共享服务侧执行,403 按信封透传"""
    config, cloud_url, ws_id = _cloud_context(req.workspace_id)
    try:
        resp = cloud_req(
            "POST",
            f"{cloud_url}/api/workspaces/{ws_id}/documents/{shared_doc_id}/tags",
            config, json={"add": req.add, "remove": req.remove}, timeout=15,
        )
        if resp.status_code != 200:
            _passthrough(resp)
        return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"共享服务不可达: {e}") from e


# ========== 下载代理(前端不直连共享后端,V-13) ==========

_PREVIEW_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_PREVIEW_BINARY_MIMES = {"application/pdf", "image/png", "image/jpeg", "image/gif",
                         "image/webp", "image/bmp"}


class _PreviewStreamingResponse(StreamingResponse):
    """Close the upstream even when the client disconnects before body iteration."""

    def __init__(self, content, *, close_upstream: Callable[[], None], **kwargs):
        super().__init__(content, **kwargs)
        self._close_upstream = close_upstream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._close_upstream()


def _preview_target(doc_id, vid, workspace_id):
    config, cloud_url, ws_id = _cloud_context(workspace_id)
    verify_server_identity(config)
    url = f"{cloud_url}/api/workspaces/{ws_id}/documents/{doc_id}/versions/{vid}/preview"
    _validate_request_target(url, config)
    return config, url


@router.get("/cloud-docs/{doc_id}/versions/{vid}/preview")
def proxy_cloud_preview(doc_id: str, vid: str, workspace_id: Optional[str] = None):
    """Read-only preview; no intake, snapshots, share links or local metadata writes."""
    from fastapi.responses import JSONResponse

    try:
        config, url = _preview_target(doc_id, vid, workspace_id)
        response = cloud_req("GET", url, config, timeout=60)
        if response.status_code != 200:
            _passthrough(response)
        return JSONResponse(content=response.json(), headers=_PREVIEW_HEADERS)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "共享服务不可达，请重试") from exc
    except CloudIdentityError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(502, "共享服务预览响应异常，请重试") from exc


@router.get("/cloud-docs/{doc_id}/versions/{vid}/preview/content")
def proxy_cloud_preview_content(doc_id: str, vid: str, workspace_id: Optional[str] = None):
    context = None
    try:
        config, url = _preview_target(doc_id, vid, workspace_id)

        def open_response():
            stream = _http.stream("GET", url + "/content", headers=auth_headers(config),
                                  timeout=httpx.Timeout(30, read=60))
            return stream, stream.__enter__()

        context, response = open_response()
        if response.status_code == 401:
            context.__exit__(None, None, None)
            context = None
            if not refresh_token(config):
                raise HTTPException(401, "共享会话已过期，请重新登录")
            context, response = open_response()
        if response.status_code != 200:
            response.read()
            _passthrough(response)
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if mime not in _PREVIEW_BINARY_MIMES:
            raise HTTPException(502, "共享服务返回了不支持的预览格式")
        max_bytes = (50 if mime == "application/pdf" else 20) * 1024 * 1024
        length = response.headers.get("content-length")
        if length is not None:
            try:
                length = int(length)
            except ValueError as exc:
                raise HTTPException(502, "共享服务预览大小信息无效") from exc
            if not 0 <= length <= max_bytes:
                raise HTTPException(502, "共享服务预览内容超过大小上限")
    except httpx.HTTPError as exc:
        if context is not None:
            context.__exit__(None, None, None)
        raise HTTPException(502, "共享服务不可达，请重试") from exc
    except CloudIdentityError as exc:
        if context is not None:
            context.__exit__(None, None, None)
        raise HTTPException(409, str(exc)) from exc
    except BaseException:
        if context is not None:
            context.__exit__(None, None, None)
        raise

    closed = False
    close_lock = threading.Lock()

    def close_upstream():
        nonlocal closed
        with close_lock:
            if closed:
                return
            closed = True
        context.__exit__(None, None, None)

    def chunks():
        received = 0
        try:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                received += len(chunk)
                if received > max_bytes:
                    # Once headers have been sent, terminate instead of forwarding
                    # bytes beyond the documented limit; the viewer reports failure.
                    raise httpx.StreamError("共享服务预览内容超过大小上限")
                yield chunk
        finally:
            close_upstream()

    headers = dict(_PREVIEW_HEADERS)
    if length is not None:
        headers["Content-Length"] = str(length)
    return _PreviewStreamingResponse(chunks(), close_upstream=close_upstream, media_type=mime, headers=headers)


@router.get("/cloud-docs/{doc_id}/versions/{vid}/download")
@_serialized
def proxy_cloud_download(doc_id: str, vid: str, workspace_id: Optional[str] = None):
    """把共享版本内容经本地服务流式转发给前端(替代已停用的预签名 URL)"""
    from fastapi.responses import StreamingResponse

    config, cloud_url, ws_id = _cloud_context(workspace_id)
    url = f"{cloud_url}/api/workspaces/{ws_id}/documents/{doc_id}/versions/{vid}/download"

    def _open():
        headers = {"Authorization": auth_headers(config)["Authorization"]}
        ctx = _http.stream("GET", url, headers=headers,
                           timeout=httpx.Timeout(30.0, read=600.0))
        return ctx, ctx.__enter__()

    ctx, resp = _open()
    if resp.status_code == 401 and refresh_token(config):
        ctx.__exit__(None, None, None)
        ctx, resp = _open()
    if resp.status_code != 200:
        resp.read()
        ctx.__exit__(None, None, None)
        _passthrough(resp)

    def _gen():
        try:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                yield chunk
        finally:
            ctx.__exit__(None, None, None)

    headers = {}
    if resp.headers.get("content-disposition"):
        headers["Content-Disposition"] = resp.headers["content-disposition"]
    return StreamingResponse(
        _gen(),
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


# M5 共享协作均经本地适配层；前端不持有共享服务令牌。
def _collaboration_request(method, path, workspace_id=None, **kwargs):
    config, cloud_url, ws = _cloud_context(workspace_id)
    try:
        response = cloud_req(method, cloud_url + path.format(ws=ws), config, **kwargs)
        if response.status_code != 200:
            _passthrough(response)
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, '共享服务不可达，请重试') from exc


@router.get('/context')
def collaboration_context(workspace_id: Optional[str] = None):
    _, server, _ = _cloud_context(workspace_id)
    result = _collaboration_request('GET', '/api/workspaces/{ws}/collaboration-context', workspace_id)
    return {**result, 'server_url': server.rstrip('/')}


@router.get('/tags/{shared_doc_id}')
def list_shared_tags(shared_doc_id: str, workspace_id: Optional[str] = None):
    return _collaboration_request('GET', '/api/workspaces/{ws}/documents/' + shared_doc_id + '/tags', workspace_id)


@router.get('/suggestions')
def shared_suggestions(type: str = 'name', q: str = '', selected_tag_ids: str = '', workspace_id: Optional[str] = None):
    return _collaboration_request('GET', '/api/workspaces/{ws}/documents/suggestions', workspace_id,
                                  params=dict(type=type, q=q, selected_tag_ids=selected_tag_ids))


@router.get('/trash')
def shared_trash(workspace_id: Optional[str] = None, page: int = 1, page_size: int = 20):
    return _collaboration_request('GET', '/api/workspaces/{ws}/trash', workspace_id, params=dict(page=page, page_size=page_size))


@router.post('/documents/{shared_doc_id}/{action}')
def manage_shared_document(shared_doc_id: str, action: Literal['trash', 'restore', 'purge'], workspace_id: Optional[str] = None):
    return _collaboration_request('POST', '/api/workspaces/{ws}/documents/' + shared_doc_id + '/' + action, workspace_id)


@router.get('/members')
def list_shared_members(workspace_id: Optional[str] = None):
    return _collaboration_request('GET', '/api/workspaces/{ws}/members-for-deactivation', workspace_id)


@router.post('/members/{user_id}/deactivate')
def deactivate_shared_member(user_id: str, workspace_id: Optional[str] = None):
    members = _collaboration_request('GET', '/api/workspaces/{ws}/members-for-deactivation', workspace_id)
    if not any(member['id'] == user_id for member in members):
        raise HTTPException(404, '成员不存在')
    return _collaboration_request('POST', '/api/admin/users/' + user_id + '/deactivate', workspace_id)
