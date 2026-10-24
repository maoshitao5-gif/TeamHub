"""
文档、版本、标签 API（工作空间范围）—— M1-R02 收敛后仅保留只读接口

写入接口（create/update/delete 文档、push、snapshot、独立 tag 增删）已停用，
统一由 decommissioned 桩返回 410（契约 §7 + 整改清单 M1-R02）；
新的发布/版本提交/标签关联通路在 M4 由发布通路实现。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from typing import List

from app.database import get_db
from app.models import (
    User, CloudDocument, CloudVersion, CloudTag, cloud_document_tags,
)
from app.core.deps import get_current_user, check_workspace_access, get_workspace_document
from app.schemas.document import (
    DocumentResponse,
    TagResponse,
    VersionResponse,
)

from app.services.access import (visible_document_clause, document_capabilities, batch_document_capabilities,
                                 validate_access_entries, replace_access)
from app.schemas.publication import AccessUpdate
from app.models import DocumentAccess, AdminAction, ServerMetadata
from app.api.publications import _begin_write

router = APIRouter(tags=["文档"])


@router.get('/api/server-info')
def server_info(db: Session = Depends(get_db)):
    from sqlalchemy.exc import IntegrityError
    metadata = db.get(ServerMetadata, 1)
    if metadata is None:
        db.add(ServerMetadata(id=1))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        metadata = db.get(ServerMetadata, 1)
    return dict(server_id=metadata.server_id, protocol_major=metadata.protocol_major,
                schema_version=metadata.schema_version,
                capabilities=['document_acl', 'fixed_deliveries', 'explicit_publication_scope'])


def _access_response(doc, db):
    return dict(document_id=doc.id, visibility=doc.visibility, access_revision=doc.access_revision,
                access=[dict(user_id=entry.user_id, role=entry.role)
                        for entry in db.query(DocumentAccess).filter_by(document_id=doc.id)
                        .order_by(DocumentAccess.user_id)])


@router.get('/api/workspaces/{ws_id}/documents/{doc_id}/access')
def get_document_access(ws_id: str, doc_id: str, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='manage')
    return _access_response(doc, db)


@router.put('/api/workspaces/{ws_id}/documents/{doc_id}/access')
def update_document_access(ws_id: str, doc_id: str, body: AccessUpdate,
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _begin_write(db, ws_id, current_user)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='manage')
    if doc.access_revision != body.base_access_revision:
        raise HTTPException(409, '资料授权已变化，请重新加载')
    grants = validate_access_entries(body.access, ws_id, db)
    replace_access(doc, grants, current_user.id, db)
    doc.visibility = body.visibility
    doc.access_revision += 1
    db.add(AdminAction(actor_id=current_user.id, action='update_document_access',
                       target=dict(document_id=doc.id, workspace_id=ws_id,
                                   access_revision=doc.access_revision, visibility=doc.visibility)))
    db.commit()
    return _access_response(doc, db)


# ========== 服务端搜索 ==========

@router.get("/api/workspaces/{ws_id}/documents/search")
async def search_documents(
    ws_id: str,
    q: str = "",
    tags: str = "",
    tag_ids: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    服务端搜索：按名称和标签过滤文档
    - q: 名称关键词（ILIKE 模糊匹配）
    - tags: 逗号分隔的标签名，AND 匹配全部
    """
    check_workspace_access(ws_id, current_user.id, "viewer", db)

    query = db.query(CloudDocument).filter(
        CloudDocument.workspace_id == ws_id,
        visible_document_clause(current_user.id),
        CloudDocument.trashed_at == None,
    )

    if q.strip():
        query = query.filter(CloudDocument.name.ilike(f"%{q.strip()}%"))

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    if tag_list:
        for tag_name in tag_list:
            tag = db.query(CloudTag).filter(
                CloudTag.workspace_id == ws_id,
                CloudTag.name == tag_name,
            ).first()
            if not tag:
                # 标签不存在，直接返回空
                return {"total": 0, "items": [], "page": page, "page_size": page_size}
            query = query.filter(
                CloudDocument.tags.any(CloudTag.id == tag.id)
            )

    for tag_id in dict.fromkeys(t.strip() for t in tag_ids.split(',') if t.strip()):
        query = query.filter(CloudDocument.tags.any(CloudTag.id == tag_id))
    total = query.count()
    docs = (
        query
        .options(selectinload(CloudDocument.tags))
        .order_by(CloudDocument.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    document_ids = [doc.id for doc in docs]
    uploader_names = dict(db.query(User.id, User.display_name).filter(
        User.id.in_({doc.uploader_id for doc in docs if doc.uploader_id})).all()) if docs else {}
    version_counts = dict(db.query(CloudVersion.document_id, func.count(CloudVersion.id)).filter(
        CloudVersion.document_id.in_(document_ids)).group_by(CloudVersion.document_id).all()) if docs else {}
    current_versions = {version.id: version for version in db.query(CloudVersion).filter(
        CloudVersion.document_id.in_(document_ids),
        CloudVersion.id.in_({doc.current_version_id for doc in docs if doc.current_version_id})).all()} if docs else {}
    capabilities = batch_document_capabilities(docs, current_user.id, db)
    items = []
    for doc in docs:
        current_ver = current_versions.get(doc.current_version_id)
        if current_ver and current_ver.document_id != doc.id:
            current_ver = None
        items.append({
            "id": doc.id,
            "name": doc.name,
            "description": doc.description,
            "is_folder": doc.is_folder,
            "status": doc.status,
            "file_count": doc.file_count,
            "total_size": doc.total_size,
            "server_version": doc.server_version,
            "uploader_id": doc.uploader_id,
            "uploader_display_name": uploader_names.get(doc.uploader_id),
            "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in doc.tags],
            "version_count": version_counts.get(doc.id, 0),
            "visibility": doc.visibility, "access_revision": doc.access_revision,
            "capabilities": capabilities[doc.id],
            "current_version": {
                "id": current_ver.id,
                "oss_key": current_ver.oss_key,
                "sha256_hash": current_ver.content_hash,
                "content_hash": current_ver.content_hash,
                "manifest_hash": current_ver.manifest_hash,
                "file_size": current_ver.file_size,
                "original_filename": current_ver.original_filename,
            } if current_ver else None,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
        })

    return {"total": total, "items": items, "page": page, "page_size": page_size}


# ========== 文档读取 ==========

@router.get("/api/workspaces/{ws_id}/documents", response_model=List[DocumentResponse])
async def list_documents(
    ws_id: str,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    q = db.query(CloudDocument).filter(
        CloudDocument.workspace_id == ws_id,
        visible_document_clause(current_user.id),
        CloudDocument.trashed_at == None,
    )
    if status:
        q = q.filter(CloudDocument.status == status)
    docs = q.options(selectinload(CloudDocument.tags), selectinload(CloudDocument.versions)).order_by(
        CloudDocument.updated_at.desc()).all()
    capabilities = batch_document_capabilities(docs, current_user.id, db)
    results = []
    for doc in docs:
        result = DocumentResponse.model_validate(doc)
        result.capabilities = capabilities[doc.id]
        for version in result.versions:
            version.is_current = version.id == doc.current_version_id
        results.append(result)
    return results


@router.get("/api/workspaces/{ws_id}/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id)
    result = DocumentResponse.model_validate(doc)
    result.capabilities = document_capabilities(doc, current_user.id, db)
    for version in result.versions:
        version.is_current = version.id == doc.current_version_id
    return result


# ========== 版本读取 ==========

@router.get("/api/workspaces/{ws_id}/documents/{doc_id}/versions", response_model=List[VersionResponse])
async def list_versions(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    # M1 修复:版本查询必须限定文档归属当前工作空间,防止跨空间读取
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id)
    versions = db.query(CloudVersion).filter(CloudVersion.document_id == doc.id).order_by(
        CloudVersion.version_number.desc()
    ).all()
    results = []
    for version in versions:
        result = VersionResponse.model_validate(version)
        result.is_current = version.id == doc.current_version_id
        results.append(result)
    return results


# ========== 标签读取 ==========

@router.get("/api/workspaces/{ws_id}/tags", response_model=List[TagResponse])
async def list_tags(
    ws_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    return (db.query(CloudTag).join(cloud_document_tags, CloudTag.id == cloud_document_tags.c.tag_id)
            .join(CloudDocument, CloudDocument.id == cloud_document_tags.c.document_id)
            .filter(CloudTag.workspace_id == ws_id, CloudDocument.workspace_id == ws_id,
        visible_document_clause(current_user.id),
                    CloudDocument.trashed_at.is_(None)).distinct().order_by(CloudTag.name).all())
