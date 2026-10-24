"""
发布 / 版本提交 / 下载 / 查重 API(M4,契约 §5.2)

发布通路:流式上传暂存 → 服务端重算摘要校验 → 短事务提交资料/版本/当前引用 → 返回成功。
- 上传 raw body 流式落暂存,服务端重算 sha256(不信任客户端声称值);
- 幂等:(actor, workspace, request_id) 唯一,同请求返回同结果;换负载 → 409 REQUEST_MISMATCH;
- 查重:授权范围内 content_hash 命中返回候选,不建档;
- 版本提交:基准版本非当前 → 409 STALE_BASE_VERSION;成功即当前版,线性历史;
- 下载:三者归属匹配后流式返回;权限与资源不存在一律 404(防探测,契约 §4)。
- 权限检查用 Depends(先于 body 校验),无权限/不存在一律 404。
"""
import hashlib
import json
import stat
import zlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User, CloudDocument, CloudVersion, CloudTag, PublicationRequest, Workspace,
    cloud_document_tags,
)
from app.core.deps import get_current_user, check_workspace_access, get_workspace_document
from app.services.access import visible_document_clause, validate_access_entries, replace_access
from app.services.storage_usage import check_quota
from app.services import preview
from app.core import object_store
from app.schemas.publication import (
    UploadResponse, PublicationCreate, VersionSubmit, DuplicateCheckRequest,
)

router = APIRouter(tags=["发布"])


class _StoredContentError(ValueError):
    """A visible existing version cannot safely be reused."""


def _err(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _storage_reuse_error():
    return _err(409, "STORAGE_ERROR", "云端既有文件缺失或损坏，请联系管理员检查后重试")


def _verify_stored_version(document, version):
    """Validate only the authorized candidate's actual immutable object before reuse."""
    if version is None or version.document_id != document.id:
        raise _StoredContentError()
    if document.doc_type != "folder" and version.manifest_hash is not None:
        raise _StoredContentError()
    digest = version.manifest_hash if document.doc_type == "folder" else version.content_hash
    if not digest:
        raise _StoredContentError()
    try:
        path = object_store.object_path(digest)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != version.file_size:
            raise _StoredContentError()
        if object_store.hash_file(path) != (digest, version.file_size):
            raise _StoredContentError()
        if document.doc_type == "folder":
            content_hash, _, _ = object_store.inspect_folder(path)
            if content_hash != version.content_hash:
                raise _StoredContentError()
        after = path.lstat()
        if not stat.S_ISREG(after.st_mode) or (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise _StoredContentError()
    except (OSError, object_store.StorageValidationError, zlib.error) as exc:
        raise _StoredContentError() from exc


def require_ws_access(min_role: str):
    """防探测权限依赖:先于 body 校验执行,无权限/不存在一律 404(契约 §4)"""
    async def _check(
        ws_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Workspace:
        try:
            return check_workspace_access(ws_id, current_user.id, min_role, db)
        except HTTPException:
            raise HTTPException(status_code=404, detail="资源不存在")
    return _check


def _begin_write(db, ws_id, user):
    # MVP SQLite: 在任何发布状态读取前取得数据库写锁，跨线程/进程串行化校验及提交。
    actor_id = user.id
    db.rollback()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        raise HTTPException(503, "共享服务忙，请使用同一请求编号重试") from exc
    try:
        check_workspace_access(ws_id, actor_id, "member", db)
    except HTTPException:
        raise HTTPException(404, "资源不存在")


def _validated_upload(body, ws_id, actor_id, doc_type, fallback_name, purpose='publication', *, promote=True):
    sidecar = object_store.load_staging(body.upload_id)
    if (not sidecar or sidecar.get("workspace_id") != ws_id
            or sidecar.get("uploader_id") != actor_id
            or sidecar.get("purpose", "publication") != purpose):
        raise HTTPException(404, "资源不存在")
    try:
        filename = body.original_filename or fallback_name
        if '/' in object_store.safe_name(filename) or filename.endswith('/'):
            raise object_store.StorageValidationError('文件名不合法')
        if doc_type != 'folder' and body.manifest_hash is not None:
            raise object_store.StorageValidationError('单文件不能声明文件夹归档摘要')
        digest, count, total = object_store.validate_upload(
            body.upload_id, sidecar, doc_type, body.content_hash, body.manifest_hash)
        key = (object_store.promote_staging(body.upload_id, digest) if promote
               else object_store.storage_key_for(digest))
    except object_store.StorageValidationError as exc:
        raise HTTPException(422, {"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}) from exc
    except OSError as exc:
        raise HTTPException(507, "共享存储不可用，请稍后重试") from exc
    return sidecar, key, count, total


def _promote_validated_upload(upload_id, digest):
    """Only create an object after deciding that this request needs a new reference."""
    try:
        return object_store.promote_staging(upload_id, digest)
    except object_store.StorageValidationError as exc:
        return _err(422, "VALIDATION_ERROR", str(exc))
    except OSError:
        return _err(507, "STORAGE_ERROR", "共享存储不可用，请稍后重试")


def _request_hash(**fields) -> str:
    payload = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _find_pub(db, actor_id, ws_id, request_id):
    return db.query(PublicationRequest).filter(
        PublicationRequest.actor_id == actor_id,
        PublicationRequest.workspace_id == ws_id,
        PublicationRequest.request_id == request_id,
    ).first()


def _record_pub(db, actor_id, ws_id, request_id, req_hash, action, status, **kw):
    pub = PublicationRequest(
        request_id=request_id, actor_id=actor_id, workspace_id=ws_id,
        request_hash=req_hash, action=action, status=status,
        result_document_id=kw.get("result_document_id"),
        result_version_id=kw.get("result_version_id"),
        error_code=kw.get("error_code"),
        error_message=kw.get("error_message"),
        completed_at=datetime.now(timezone.utc) if status != "pending" else None,
    )
    db.add(pub)
    db.flush()
    return pub


def _pub_result(pub: PublicationRequest, document=None) -> dict:
    """发布结果;同时提供 document_id/version_id(POST 用)与 result_* 别名(GET 用)"""
    result = {
        "request_id": pub.request_id,
        "status": pub.status,
        "code": pub.error_code,
        "document_id": pub.result_document_id,
        "version_id": pub.result_version_id,
        "result_document_id": pub.result_document_id,
        "result_version_id": pub.result_version_id,
        "error": (
            {"code": pub.error_code, "message": pub.error_message}
            if pub.error_code and pub.status != "succeeded" else None
        ),
    }
    if pub.error_code == "DUPLICATE_CONTENT" and document is not None:
        result["duplicate_document"] = _doc_brief(document)
    return result


def _check_publication_result(pub, user_id, db):
    if pub.result_document_id:
        document = get_workspace_document(pub.workspace_id, pub.result_document_id, db, user_id=user_id)
        if pub.error_code in ("DUPLICATE_CONTENT", "CONTENT_UNCHANGED"):
            version_id = (document.current_version_id if pub.error_code == "DUPLICATE_CONTENT"
                          else pub.result_version_id)
            _verify_stored_version(document, db.get(CloudVersion, version_id))
        return document


def _find_duplicate(db, ws_id, doc_type, content_hash, user_id):
    entity_types = ("folder",) if doc_type == "folder" else ("file", "archive")
    candidate = (
        db.query(CloudDocument, CloudVersion)
        .join(CloudVersion, (CloudVersion.document_id == CloudDocument.id) &
              (CloudVersion.id == CloudDocument.current_version_id))
        .filter(
            CloudDocument.workspace_id == ws_id,
            visible_document_clause(user_id),
            CloudDocument.doc_type.in_(entity_types),
            CloudDocument.trashed_at.is_(None),
            CloudDocument.status != "trashed",
            CloudVersion.content_hash == content_hash,
        )
        .order_by(CloudDocument.created_at, CloudDocument.id)
        .first()
    )
    if candidate:
        document, version = candidate
        _verify_stored_version(document, version)
        return document
    return None


def _attach_tags(db, ws_id, doc, tag_names, user_id):
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = db.query(CloudTag).filter(
            CloudTag.workspace_id == ws_id, CloudTag.name == name
        ).first()
        if not tag:
            tag = CloudTag(workspace_id=ws_id, name=name)
            db.add(tag)
            db.flush()
        already = db.execute(cloud_document_tags.select().where(
            (cloud_document_tags.c.document_id == doc.id) &
            (cloud_document_tags.c.tag_id == tag.id)
        )).first()
        if already:
            continue
        db.execute(cloud_document_tags.insert().values(
            document_id=doc.id, tag_id=tag.id, added_by=user_id,
            created_at=datetime.now(timezone.utc),
        ))


def _doc_brief(doc: CloudDocument) -> dict:
    current = next((v for v in doc.versions if v.id == doc.current_version_id), None)
    return {
        "document_id": doc.id,
        "name": doc.name,
        "doc_type": doc.doc_type,
        "current_version_id": doc.current_version_id,
        "current_version_number": current.version_number if current else None,
        "total_size": doc.total_size,
    }


# ========== 上传 ==========

@router.post("/api/workspaces/{ws_id}/uploads", response_model=UploadResponse)
async def upload_content(
    ws_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("member")),
    filename: str = Query(""),
    purpose: str = Query('publication', pattern='^(publication|delivery)$'),
):
    try:
        sidecar = await object_store.save_upload_stream(
            request.stream(), workspace_id=ws_id, uploader_id=current_user.id,
            filename=filename, purpose=purpose,
        )
    except object_store.StagingCapacityError as e:
        return _err(507, "STAGING_FULL", str(e))
    except object_store.StorageValidationError as e:
        return _err(422, "VALIDATION_ERROR", str(e))
    except OSError:
        return _err(507, "STORAGE_ERROR", "上传写入失败，请稍后重试")
    return UploadResponse(
        upload_id=sidecar["upload_id"], sha256=sidecar["sha256"],
        file_size=sidecar["file_size"],
    )


# ========== 发布 ==========

@router.post("/api/workspaces/{ws_id}/publications")
def create_publication(
    ws_id: str,
    body: PublicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("member")),
):
    if not body.confirmed:
        return _err(400, "CONFIRMATION_REQUIRED", "请确认共享范围和随行标签后发布")
    if body.visibility is None:
        return _err(400, "SCOPE_REQUIRED", "请选择全空间或指定成员后再发布")
    _begin_write(db, ws_id, current_user)
    req_hash = _request_hash(
        action="publish", name=body.name, doc_type=body.doc_type,
        description=body.description, force_new=body.force_new,
        content_hash=body.content_hash, manifest_hash=body.manifest_hash, original_filename=body.original_filename,
        tags=sorted(body.tags), note=body.note or "", visibility=body.visibility,
        access=sorted((e.user_id, e.role) for e in body.access),
    )

    existing = _find_pub(db, current_user.id, ws_id, body.request_id)
    if existing:
        try:
            result_document = _check_publication_result(existing, current_user.id, db)
        except _StoredContentError:
            return _storage_reuse_error()
        if existing.request_hash != req_hash:
            return _err(409, "REQUEST_MISMATCH", "同 request_id 携带不同内容摘要,拒绝替换")
        return _pub_result(existing, result_document)

    grants = validate_access_entries(body.access, ws_id, db, creator_id=current_user.id)
    try:
        sidecar, storage_key, count, total = _validated_upload(
            body, ws_id, current_user.id, body.doc_type, body.name, promote=False)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        raise
    try:
        dup = _find_duplicate(db, ws_id, body.doc_type, body.content_hash, current_user.id)
    except _StoredContentError:
        return _storage_reuse_error()
    if dup:
        pub = _record_pub(db, current_user.id, ws_id, body.request_id, req_hash, "publish",
                          "failed", result_document_id=dup.id,
                          error_code="DUPLICATE_CONTENT", error_message="云端已存在相同文件，未重复新增资料")
        db.commit()
        return _pub_result(pub, dup)
    size = sidecar["file_size"]
    check_quota(db, _ws.team_id, size)
    promoted = _promote_validated_upload(body.upload_id, sidecar["sha256"])
    if isinstance(promoted, JSONResponse):
        return promoted
    storage_key = promoted

    doc = CloudDocument(
        workspace_id=ws_id, name=body.name, description=body.description,
        doc_type=body.doc_type, status="organized",
        is_folder=body.doc_type == "folder", file_count=count, total_size=total,
        created_by=current_user.id, uploader_id=current_user.id,
        visibility=body.visibility,
    )
    db.add(doc)
    db.flush()
    replace_access(doc, grants, current_user.id, db)
    version = CloudVersion(
        document_id=doc.id, version_number=1,
        content_hash=body.content_hash, manifest_hash=body.manifest_hash, storage_key=storage_key,
        file_size=size, original_filename=body.original_filename or body.name,
        note=body.note, author_id=current_user.id, is_current=False,
    )
    db.add(version)
    db.flush()
    doc.current_version_id = version.id
    _attach_tags(db, ws_id, doc, body.tags, current_user.id)
    pub = _record_pub(db, current_user.id, ws_id, body.request_id, req_hash, "publish",
                      "succeeded", result_document_id=doc.id, result_version_id=version.id)
    db.commit()
    return _pub_result(pub)


@router.get("/api/workspaces/{ws_id}/publications/{request_id}")
def get_publication(
    ws_id: str,
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("viewer")),
):
    pub = _find_pub(db, current_user.id, ws_id, request_id)
    if not pub:
        return _err(404, "NOT_FOUND", "发布请求不存在")
    try:
        result_document = _check_publication_result(pub, current_user.id, db)
    except _StoredContentError:
        return _storage_reuse_error()
    return _pub_result(pub, result_document)


# ========== 版本提交 ==========

@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/versions")
def submit_version(
    ws_id: str,
    doc_id: str,
    body: VersionSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("member")),
):
    if not body.confirmed:
        return _err(400, "CONFIRMATION_REQUIRED", "请确认向组织提交新版本")
    _begin_write(db, ws_id, current_user)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='contribute')
    req_hash = _request_hash(
        action="submit_version", document_id=doc_id, base_version_id=body.base_version_id,
        content_hash=body.content_hash, manifest_hash=body.manifest_hash, original_filename=body.original_filename, note=body.note or "",
    )

    existing = _find_pub(db, current_user.id, ws_id, body.request_id)
    if existing:
        if existing.request_hash != req_hash:
            return _err(409, "REQUEST_MISMATCH", "同 request_id 携带不同内容摘要,拒绝替换")
        try:
            _check_publication_result(existing, current_user.id, db)
        except _StoredContentError:
            return _storage_reuse_error()
        return _pub_result(existing)

    if doc.current_version_id != body.base_version_id:
        return _err(409, "STALE_BASE_VERSION", "基准版本已过期",
                    {"current_version_id": doc.current_version_id})

    try:
        sidecar, storage_key, count, total = _validated_upload(
            body, ws_id, current_user.id, doc.doc_type, doc.name, promote=False)
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        raise
    current = db.get(CloudVersion, doc.current_version_id)
    if current and current.content_hash == body.content_hash:
        try:
            _verify_stored_version(doc, current)
        except _StoredContentError:
            return _storage_reuse_error()
        pub = _record_pub(db, current_user.id, ws_id, body.request_id, req_hash, "submit_version",
                          "succeeded", result_document_id=doc.id, result_version_id=current.id,
                          error_code="CONTENT_UNCHANGED", error_message="内容未变，沿用当前版本")
        db.commit()
        return _pub_result(pub)
    check_quota(db, _ws.team_id, sidecar['file_size'])
    promoted = _promote_validated_upload(body.upload_id, sidecar["sha256"])
    if isinstance(promoted, JSONResponse):
        return promoted
    storage_key = promoted
    next_num = (db.query(func.max(CloudVersion.version_number))
                .filter(CloudVersion.document_id == doc.id).scalar() or 0) + 1
    version = CloudVersion(
        document_id=doc.id, version_number=next_num,
        content_hash=body.content_hash, manifest_hash=body.manifest_hash, storage_key=storage_key,
        file_size=sidecar["file_size"], original_filename=body.original_filename or doc.name,
        note=body.note, author_id=current_user.id,
        base_version_id=body.base_version_id, is_current=False,
    )
    db.add(version)
    db.flush()
    doc.current_version_id = version.id
    doc.total_size = total
    doc.file_count = count
    doc.updated_at = datetime.now(timezone.utc)
    pub = _record_pub(db, current_user.id, ws_id, body.request_id, req_hash, "submit_version",
                      "succeeded", result_document_id=doc.id, result_version_id=version.id)
    db.commit()
    return _pub_result(pub)


# ========== 下载 ==========

@router.get("/api/workspaces/{ws_id}/documents/{doc_id}/versions/{vid}/download")
def download_version(
    ws_id: str,
    doc_id: str,
    vid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("viewer")),
):
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id)
    version = db.query(CloudVersion).filter(
        CloudVersion.id == vid, CloudVersion.document_id == doc.id
    ).first()
    if not version:
        return _err(404, "NOT_FOUND", "版本不存在")
    if not version.content_hash:
        return _err(404, "NOT_FOUND", "版本内容缺失")
    obj_path = object_store.object_path(version.manifest_hash or version.content_hash)
    if not obj_path.is_file():
        return _err(404, "NOT_FOUND", "版本内容缺失")
    try:
        if object_store.hash_file(obj_path) != (version.manifest_hash or version.content_hash, version.file_size):
            return _err(422, "STORAGE_ERROR", "版本内容校验失败")
    except OSError:
        return _err(507, "STORAGE_ERROR", "版本内容读取失败")
    return FileResponse(obj_path, media_type="application/octet-stream",
                        filename=version.original_filename or doc.name)


def _preview_version(ws_id, doc_id, vid, user, db):
    document = get_workspace_document(ws_id, doc_id, db, user_id=user.id)
    version = db.query(CloudVersion).filter(
        CloudVersion.id == vid, CloudVersion.document_id == document.id
    ).first()
    if not version:
        raise HTTPException(404, "版本不存在")
    return document, version


@router.get("/api/workspaces/{ws_id}/documents/{doc_id}/versions/{vid}/preview")
def preview_version(
    ws_id: str, doc_id: str, vid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("viewer")),
):
    document, version = _preview_version(ws_id, doc_id, vid, current_user, db)
    try:
        content = preview.preview_metadata(document, version)
        return JSONResponse(content=content, headers=preview.PREVIEW_HEADERS)
    except preview.PreviewError as exc:
        response = _err(exc.status_code, "PREVIEW_ERROR", str(exc))
        response.headers.update(preview.PREVIEW_HEADERS)
        return response


@router.get("/api/workspaces/{ws_id}/documents/{doc_id}/versions/{vid}/preview/content")
def preview_version_content(
    ws_id: str, doc_id: str, vid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("viewer")),
):
    document, version = _preview_version(ws_id, doc_id, vid, current_user, db)
    try:
        path, mime = preview.preview_content(document, version)
        return FileResponse(path, media_type=mime, headers=preview.PREVIEW_HEADERS)
    except preview.PreviewError as exc:
        response = _err(exc.status_code, "PREVIEW_ERROR", str(exc))
        response.headers.update(preview.PREVIEW_HEADERS)
        return response



# ========== 查重 ==========

@router.post("/api/workspaces/{ws_id}/duplicates")
def check_duplicates(
    ws_id: str,
    body: DuplicateCheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _ws: Workspace = Depends(require_ws_access("viewer")),
):
    try:
        dup = _find_duplicate(db, ws_id, body.doc_type, body.content_hash, current_user.id)
    except _StoredContentError:
        return _storage_reuse_error()
    if not dup:
        return []
    return [_doc_brief(dup)]
