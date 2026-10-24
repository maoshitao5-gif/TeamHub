"""Desktop delivery adapter: explicit send/intake; never background-sync a receipt."""
import json
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import Document, Version, RemoteOperation
from backend.app.core.cloud_client import cloud_req, verify_server_identity, auth_headers, _http
from backend.app.core.storage_paths import pending_root, relative_location
from backend.app.core.file_utils import get_document_or_404
from backend.app.core.logger import get_logger
from backend.app.api.sharing import (_cloud_context, _ensure_fresh_token, _passthrough, _serialized,
                                    _stream_to_file, _object_path_of, _verified_identity)
from backend.app.services import snapshots, versions, content_duplicates
from backend.app.services.remote_update import freeze_operation, record_source

logger = get_logger('api.deliveries')
router = APIRouter(prefix='/api/deliveries', tags=['资料投递'])


class SendRequest(BaseModel):
    doc_id: str
    version_id: Optional[str] = None
    workspace_id: Optional[str] = None
    recipient_ids: list[str] = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=4000)
    request_id: str = Field(min_length=1, max_length=128)


class IntakeRequest(BaseModel):
    workspace_id: Optional[str] = None
    request_id: str = Field(min_length=1, max_length=128)


def context(workspace_id=None):
    config, url, ws = _cloud_context(workspace_id)
    server_id, actor = _verified_identity(config)
    return config, url, ws, server_id, actor


def remote(method, path, workspace_id=None, **kwargs):
    config, url, ws, _, _ = context(workspace_id)
    response = cloud_req(method, f'{url}/api/workspaces/{ws}{path}', config, **kwargs)
    if response.status_code != 200:
        _passthrough(response)
    return response.json()


@router.get('/members')
@_serialized
def members(workspace_id: Optional[str] = None):
    return remote('GET', '/delivery-members', workspace_id)


@router.get('/unread-count')
@_serialized
def unread(workspace_id: Optional[str] = None):
    return remote('GET', '/deliveries/unread-count', workspace_id)


@router.get('/operations')
@_serialized
def operations(workspace_id: Optional[str] = None, db: Session = Depends(get_db)):
    _, _, ws, server_id, actor = context(workspace_id)
    rows = db.query(RemoteOperation).filter(RemoteOperation.server_id == server_id,
        RemoteOperation.account_id == actor, RemoteOperation.workspace_id == ws,
        RemoteOperation.kind.in_(['delivery_send', 'delivery_intake']),
        RemoteOperation.status != 'succeeded').order_by(RemoteOperation.created_at.desc()).yield_per(100)
    visible = []
    for row in rows:
        if row.status == 'failed' and row.result_json:
            try:
                result = json.loads(row.result_json)
            except ValueError:
                # A damaged record is unresolved, so keep it visible and report
                # the parse failure instead of hiding a possibly pending action.
                logger.warning('投递操作结果记录无法解析，保留待核对操作: %s', row.request_id)
            else:
                if (row.kind == 'delivery_intake' and isinstance(result, dict)
                        and result.get('code') == 'DUPLICATE_CONTENT'
                        and result.get('status') == 'duplicate' and result.get('duplicate') is True):
                    continue
        visible.append(dict(request_id=row.request_id, kind=row.kind, status=row.status, created_at=row.created_at))
        if len(visible) == 50:
            break
    return visible


@router.post('/operations/{request_id}/retry')
@_serialized
def retry_operation(request_id: str, workspace_id: Optional[str] = None, db: Session = Depends(get_db)):
    _, _, ws, server_id, actor = context(workspace_id)
    operation = db.query(RemoteOperation).filter_by(server_id=server_id, account_id=actor,
                     workspace_id=ws, request_id=request_id).first()
    if not operation or operation.kind not in ('delivery_send', 'delivery_intake'):
        raise HTTPException(404, '操作不存在')
    payload = json.loads(operation.input_json)
    if operation.kind == 'delivery_send':
        return send(SendRequest(**payload['requested'], workspace_id=ws, request_id=request_id), db)
    return intake(payload['delivery_id'], payload['item_id'], IntakeRequest(workspace_id=ws, request_id=request_id), db)


@router.get('')
@_serialized
def listing(workspace_id: Optional[str] = None, box: str = 'inbox', page: int = 1, page_size: int = 20):
    return remote('GET', '/deliveries', workspace_id, params=dict(box=box, page=page, page_size=page_size))


@router.post('/send')
@_serialized
def send(req: SendRequest, db: Session = Depends(get_db)):
    config, url, ws, server_id, actor = context(req.workspace_id)
    requested = req.model_dump(exclude={'workspace_id', 'request_id'})
    requested['recipient_ids'] = sorted(set(req.recipient_ids))
    old = db.query(RemoteOperation).filter_by(server_id=server_id, account_id=actor, workspace_id=ws, request_id=req.request_id).first()
    if old:
        payload = json.loads(old.input_json)
        if old.kind != 'delivery_send' or payload['requested'] != requested:
            raise HTTPException(409, '同一请求不能更换资料、版本或收件人')
    else:
        doc = get_document_or_404(db, req.doc_id)
        if doc.status not in ('pending', 'organized'):
            raise HTTPException(409, '只能投递待整理或文档库中的资料')
        if req.version_id:
            version = db.query(Version).filter_by(id=req.version_id, document_id=doc.id).first()
            if not version:
                raise HTTPException(404, '版本不存在')
        else:
            version, _ = versions.snapshot_working_copy(db, doc, note='投递前保存工作内容')
            db.commit()
        payload = dict(requested=requested, version_id=version.id, object_path=str(_object_path_of(version)),
                       body=dict(request_id=req.request_id, recipient_ids=requested['recipient_ids'], note=req.note,
                                 original_filename=version.original_filename or doc.name, doc_type=doc.doc_type,
                                 content_hash=version.sha256_hash, manifest_hash=version.manifest_hash))
    operation = freeze_operation(db, server_id=server_id, account_id=actor, workspace_id=ws,
                                 request_id=req.request_id, kind='delivery_send', payload=payload, local_document_id=req.doc_id)
    # Always check current server result, including recall, before replaying uploads.
    result = cloud_req('GET', f'{url}/api/workspaces/{ws}/delivery-requests/{req.request_id}', config)
    if result.status_code == 404:
        obj = Path(payload['object_path'])
        expected = payload['body']['manifest_hash'] or payload['body']['content_hash']
        if not obj.is_file() or snapshots._hash_file(obj) != expected:
            raise HTTPException(409, '冻结的投递快照缺失或损坏，未发送')
        with obj.open('rb') as stream:
            upload = _http.post(f'{url}/api/workspaces/{ws}/uploads',
                               params=dict(filename=payload['body']['original_filename'], purpose='delivery'),
                               content=stream, headers=auth_headers(config), timeout=600)
        if upload.status_code != 200:
            _passthrough(upload)
        result = cloud_req('POST', f'{url}/api/workspaces/{ws}/deliveries', config,
                           json={**payload['body'], 'upload_id': upload.json()['upload_id']}, timeout=600)
    if result.status_code != 200:
        _passthrough(result)
    operation.status = 'succeeded'
    operation.result_json = json.dumps(result.json(), ensure_ascii=False)
    db.commit()
    return result.json()


@router.get('/{delivery_id}')
@_serialized
def detail(delivery_id: str, workspace_id: Optional[str] = None):
    return remote('GET', f'/deliveries/{delivery_id}', workspace_id)


@router.post('/{delivery_id}/read')
@_serialized
def mark_read(delivery_id: str, workspace_id: Optional[str] = None):
    return remote('POST', f'/deliveries/{delivery_id}/read', workspace_id)


@router.post('/{delivery_id}/recall')
@_serialized
def recall(delivery_id: str, workspace_id: Optional[str] = None):
    return remote('POST', f'/deliveries/{delivery_id}/recall', workspace_id)


@router.get('/{delivery_id}/items/{item_id}/download')
@_serialized
def download(delivery_id: str, item_id: str, workspace_id: Optional[str] = None):
    config, url, ws, _, _ = context(workspace_id)
    stream = _http.stream('GET', f'{url}/api/workspaces/{ws}/deliveries/{delivery_id}/items/{item_id}/download',
                          headers=auth_headers(config), timeout=600)
    response = stream.__enter__()
    if response.status_code != 200:
        response.read()
        stream.__exit__(None, None, None)
        _passthrough(response)
    def chunks():
        try:
            yield from response.iter_bytes(1024 * 1024)
        finally:
            stream.__exit__(None, None, None)
    return StreamingResponse(chunks(), media_type='application/octet-stream',
                             headers={'Cache-Control': 'no-store', 'Content-Disposition': response.headers.get('content-disposition', 'attachment')})


@router.post('/{delivery_id}/items/{item_id}/intake')
@_serialized
def intake(delivery_id: str, item_id: str, req: IntakeRequest, db: Session = Depends(get_db)):
    config, url, ws, server_id, actor = context(req.workspace_id)
    payload = dict(delivery_id=delivery_id, item_id=item_id)
    operation = db.query(RemoteOperation).filter_by(server_id=server_id, account_id=actor,
                      workspace_id=ws, request_id=req.request_id).first()
    if operation:
        if operation.kind != 'delivery_intake' or json.loads(operation.input_json) != payload:
            raise HTTPException(409, '同一请求不能更换投递或附件')
        if operation.status == 'succeeded':
            return json.loads(operation.result_json)
    info = remote('GET', f'/deliveries/{delivery_id}', ws)
    if not info['can_download']:
        raise HTTPException(410, '附件已撤回或无获取权限')
    item = next((i for i in info['items'] if i['id'] == item_id), None)
    if not item:
        raise HTTPException(404, '附件不存在')
    if not isinstance(item.get('file_size'), int) or not 0 <= item['file_size'] <= settings.shared_download_max_bytes:
        raise HTTPException(413, '附件超过本机接收大小上限')
    name = item['filename']
    if Path(name).name != name or name in ('.', '..') or '\\' in name:
        raise HTTPException(422, '附件名称不合法')
    library, folder = Path(settings.library_path), item['doc_type'] == 'folder'
    # A succeeded receipt is replayed above; otherwise do not manufacture a
    # receipt/source association for an independently existing local entity.
    duplicate = content_duplicates.find_current_duplicate(
        db, content_hash=item.get('content_hash'), file_size=item['file_size'], is_folder=folder)
    if duplicate:
        result = content_duplicates.duplicate_result(duplicate)
        if operation:
            # A previous interrupted intake may already have frozen recovery
            # bytes. Keep those files, but do not create a second registered
            # entity after another intake/pull has supplied the same content.
            operation.status = 'failed'
            operation.error = result['message']
            operation.result_json = json.dumps(result, ensure_ascii=False)
            db.commit()
        return result
    operation = freeze_operation(db, server_id=server_id, account_id=actor, workspace_id=ws,
                                 request_id=req.request_id, kind='delivery_intake', payload=payload)
    root = pending_root()
    root.mkdir(parents=True, exist_ok=True)
    recovery = json.loads(operation.recovery_json) if operation.recovery_json else None
    if not recovery:
        with tempfile.TemporaryDirectory(prefix='teamhub-delivery-') as temp:
            archive = Path(temp) / 'attachment'
            with _http.stream('GET', f'{url}/api/workspaces/{ws}/deliveries/{delivery_id}/items/{item_id}/download',
                              headers=auth_headers(config), timeout=600) as response:
                if response.status_code != 200:
                    response.read()
                    _passthrough(response)
                _stream_to_file(response, archive, item['manifest_hash'] or item['content_hash'], item['file_size'])
            if folder:
                unpacked = Path(temp) / 'unpacked'
                unpacked.mkdir()
                snapshots._extract_zip_safely(archive, unpacked)
                prepared = snapshots.store_folder_snapshot(library, unpacked)
                if prepared[1] != item['content_hash']:
                    raise HTTPException(422, '附件内容摘要不符')
            else:
                key, digest, size = snapshots.store_file_snapshot(library, archive)
                prepared = (key, digest, None, size)
            candidate = f'{Path(name).stem}_{uuid.uuid4().hex}{Path(name).suffix}'
            recovery = dict(prepared=list(prepared), name=candidate, document_id=str(uuid.uuid4()))
            operation.recovery_json = json.dumps(recovery, ensure_ascii=False)
            db.commit()
    prepared = tuple(recovery['prepared'])
    path = root / recovery['name']
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            raise HTTPException(409, '待整理位置已被修改，请保留附件并检查')
        current = snapshots.store_folder_snapshot(library, path)[1] if folder else snapshots._hash_file(path)
        if current != item['content_hash']:
            raise HTTPException(409, '收录期间目标文件被修改，已保留双方内容，请检查')
    else:
        snapshots.export_object(library, prepared[0], recovery['name'], root, folder)
    doc = Document(id=recovery['document_id'], name=name, description=info['note'], is_folder=folder,
                   doc_type=item['doc_type'], status='pending', storage_area='pending', storage_mode='copy',
                   storage_path=relative_location(path, 'pending'), original_path=f'delivery://{server_id}/{delivery_id}/{item_id}')
    db.add(doc)
    db.flush()
    version, _ = versions.snapshot_working_copy(db, doc, prepared_snapshot=prepared, note='收录投递附件')
    record_source(db, doc, version, server_id=server_id, account_id=actor, workspace_id=ws, source_type='delivery',
                  delivery_id=delivery_id, item_id=item_id, operation_id=operation.id)
    result = dict(document_id=doc.id, status='pending', name=doc.name)
    operation.result_json = json.dumps(result, ensure_ascii=False)
    operation.status = 'succeeded'
    db.commit()
    return result
