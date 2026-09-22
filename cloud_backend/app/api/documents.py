"""
文档、版本、标签 API（工作空间范围）
CRUD /api/workspaces/{ws_id}/documents
CRUD /api/workspaces/{ws_id}/documents/{doc_id}/versions
CRUD /api/workspaces/{ws_id}/tags
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from typing import List

from app.database import get_db
from app.models import (
    User, CloudDocument, CloudVersion, CloudTag,
    CloudChangeLog, cloud_document_tags, _gen_uuid,
)
from app.core.deps import get_current_user, check_workspace_access, next_sequence_number
from app.schemas.document import (
    DocumentCreate, DocumentUpdate, DocumentResponse,
    TagCreate, TagResponse,
    VersionResponse, DocPushRequest,
)

router = APIRouter(tags=["文档"])


def _record_change(ws_id: str, entity_type: str, entity_id: str, operation: str,
                   payload: dict, device_id: str | None, db: Session):
    """记录一条变更日志（在 db.commit 之前调用）"""
    seq = next_sequence_number(ws_id, db)
    log = CloudChangeLog(
        workspace_id=ws_id,
        sequence_number=seq,
        entity_type=entity_type,
        entity_id=entity_id,
        operation=operation,
        payload=payload,
        device_id=device_id,
    )
    db.add(log)


# ========== 服务端搜索 ==========

@router.get("/api/workspaces/{ws_id}/documents/search")
async def search_documents(
    ws_id: str,
    q: str = "",
    tags: str = "",
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

    total = query.count()
    docs = (
        query
        .options(selectinload(CloudDocument.tags), selectinload(CloudDocument.versions))
        .order_by(CloudDocument.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for doc in docs:
        uploader_name = None
        if doc.uploader_id:
            uploader = db.query(User.display_name).filter(User.id == doc.uploader_id).first()
            if uploader:
                uploader_name = uploader.display_name
        current_ver = next((v for v in doc.versions if v.is_current), None)
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
            "uploader_display_name": uploader_name,
            "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in doc.tags],
            "version_count": len(doc.versions),
            "current_version": {
                "id": current_ver.id,
                "oss_key": current_ver.oss_key,
                "sha256_hash": current_ver.sha256_hash,
                "file_size": current_ver.file_size,
                "original_filename": current_ver.original_filename,
            } if current_ver else None,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
        })

    return {"total": total, "items": items, "page": page, "page_size": page_size}


# ========== 文档 CRUD ==========

@router.post("/api/workspaces/{ws_id}/documents", response_model=DocumentResponse, status_code=201)
async def create_document(
    ws_id: str,
    body: DocumentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "member", db)

    if db.query(CloudDocument).filter(CloudDocument.id == body.id).first():
        raise HTTPException(status_code=409, detail="文档 ID 已存在（重复推送）")

    doc = CloudDocument(
        id=body.id,
        workspace_id=ws_id,
        name=body.name,
        description=body.description,
        is_folder=body.is_folder,
        status=body.status,
        file_count=body.file_count,
        total_size=body.total_size,
    )
    db.add(doc)
    _record_change(ws_id, "document", doc.id, "create", {"name": doc.name, "status": doc.status}, None, db)
    db.commit()
    db.refresh(doc)
    return doc


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
        CloudDocument.trashed_at == None,
    )
    if status:
        q = q.filter(CloudDocument.status == status)
    return q.order_by(CloudDocument.updated_at.desc()).all()


@router.get("/api/workspaces/{ws_id}/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    doc = db.query(CloudDocument).filter(
        CloudDocument.id == doc_id, CloudDocument.workspace_id == ws_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.put("/api/workspaces/{ws_id}/documents/{doc_id}", response_model=DocumentResponse)
async def update_document(
    ws_id: str,
    doc_id: str,
    body: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "member", db)
    doc = db.query(CloudDocument).filter(
        CloudDocument.id == doc_id, CloudDocument.workspace_id == ws_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    if body.name is not None:
        doc.name = body.name
    if body.description is not None:
        doc.description = body.description
    if body.status is not None:
        doc.status = body.status
    if body.file_count is not None:
        doc.file_count = body.file_count
    if body.total_size is not None:
        doc.total_size = body.total_size
    doc.server_version += 1

    _record_change(ws_id, "document", doc.id, "update", {"name": doc.name, "status": doc.status}, None, db)
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/api/workspaces/{ws_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "member", db)
    doc = db.query(CloudDocument).filter(
        CloudDocument.id == doc_id, CloudDocument.workspace_id == ws_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    _record_change(ws_id, "document", doc.id, "delete", {}, None, db)
    db.delete(doc)
    db.commit()


# ========== 版本 CRUD ==========

@router.get("/api/workspaces/{ws_id}/documents/{doc_id}/versions", response_model=List[VersionResponse])
async def list_versions(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    return db.query(CloudVersion).filter(CloudVersion.document_id == doc_id).order_by(
        CloudVersion.version_number.desc()
    ).all()


@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/versions/snapshot", status_code=201)
async def create_version_snapshot(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    推送前保存旧版快照：将当前 is_current=True 的 CloudVersion 复制为历史记录（is_current=False）。
    OSS 文件不复制（oss_key 不变，文件已在 OSS 中持久存储）。
    调用方：本地后端在推送新版本前调用，确保旧版本可被追溯。
    """
    check_workspace_access(ws_id, current_user.id, "member", db)
    doc = db.query(CloudDocument).filter(
        CloudDocument.id == doc_id, CloudDocument.workspace_id == ws_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    current_ver = db.query(CloudVersion).filter(
        CloudVersion.document_id == doc_id,
        CloudVersion.is_current == True,
    ).first()
    if not current_ver:
        # 无历史版本可保存（首次推送），直接返回
        return {"snapshot_created": False, "reason": "no_current_version"}

    snapshot = CloudVersion(
        id=_gen_uuid(),
        document_id=doc_id,
        version_number=current_ver.version_number,
        oss_key=current_ver.oss_key,
        sha256_hash=current_ver.sha256_hash,
        file_size=current_ver.file_size,
        original_filename=current_ver.original_filename,
        is_current=False,  # 历史记录
        created_at=current_ver.created_at,
    )
    db.add(snapshot)
    db.commit()

    return {"snapshot_created": True, "version_number": snapshot.version_number}


# ========== 简化推送端点 ==========

@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/push", status_code=200)
async def push_document(
    ws_id: str,
    doc_id: str,
    body: DocPushRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    本地后端推送单个文档到云端（创建或更新）。
    - 文档不存在 → 创建 CloudDocument + 第一个 CloudVersion(is_current=True)
    - 文档已存在 → 旧 CloudVersion(is_current=True) 改为历史版本，创建新 CloudVersion
    - 标签自动合并到工作空间标签库，并关联到文档
    """
    check_workspace_access(ws_id, current_user.id, "member", db)

    doc = db.query(CloudDocument).filter(
        CloudDocument.id == doc_id, CloudDocument.workspace_id == ws_id
    ).first()

    if not doc:
        # 首次推送：创建文档
        doc = CloudDocument(
            id=doc_id,
            workspace_id=ws_id,
            name=body.name,
            description=body.description,
            is_folder=body.is_folder,
            status=body.status,
            file_count=body.file_count,
            total_size=body.total_size,
            uploader_id=current_user.id,
        )
        db.add(doc)
        db.flush()
        _record_change(ws_id, "document", doc.id, "create",
                       {"name": doc.name, "status": doc.status}, body.device_id, db)
    else:
        # 再次推送：更新文档元数据，归档旧版本
        doc.name = body.name
        doc.description = body.description
        doc.status = body.status
        doc.file_count = body.file_count
        doc.total_size = body.total_size
        doc.server_version += 1

        # 将旧 is_current 版本标为历史
        db.query(CloudVersion).filter(
            CloudVersion.document_id == doc_id,
            CloudVersion.is_current == True,
        ).update({"is_current": False})
        db.flush()

        _record_change(ws_id, "document", doc.id, "update",
                       {"name": doc.name, "status": doc.status}, body.device_id, db)

    # 合并标签
    for tag_name in body.tags:
        tag = db.query(CloudTag).filter(
            CloudTag.workspace_id == ws_id,
            CloudTag.name == tag_name,
        ).first()
        if not tag:
            tag = CloudTag(workspace_id=ws_id, name=tag_name)
            db.add(tag)
            db.flush()
        if tag not in doc.tags:
            doc.tags.append(tag)

    # 创建新版本（文件夹文档允许 oss_key 为空）
    if body.oss_key or body.is_folder:
        ver_count = db.query(CloudVersion).filter(
            CloudVersion.document_id == doc_id
        ).count()
        new_ver = CloudVersion(
            id=_gen_uuid(),
            document_id=doc_id,
            version_number=ver_count + 1,
            oss_key=body.oss_key,
            sha256_hash=body.sha256_hash,
            file_size=body.file_size,
            original_filename=body.original_filename or body.name,
            note=body.version_note or None,
            is_current=True,
        )
        db.add(new_ver)
        _record_change(ws_id, "version", new_ver.id, "create",
                       {"document_id": doc_id, "oss_key": body.oss_key}, body.device_id, db)

    db.commit()
    db.refresh(doc)
    return {"ok": True, "doc_id": doc.id, "server_version": doc.server_version}


# ========== 标签 CRUD ==========

@router.post("/api/workspaces/{ws_id}/tags", response_model=TagResponse, status_code=201)
async def create_tag(
    ws_id: str,
    body: TagCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "member", db)
    if db.query(CloudTag).filter(
        CloudTag.workspace_id == ws_id, CloudTag.name == body.name
    ).first():
        raise HTTPException(status_code=400, detail="标签名称已存在")

    tag = CloudTag(workspace_id=ws_id, name=body.name, color=body.color)
    db.add(tag)
    _record_change(ws_id, "tag", tag.id, "create", {"name": tag.name}, None, db)
    db.commit()
    db.refresh(tag)
    return tag


@router.get("/api/workspaces/{ws_id}/tags", response_model=List[TagResponse])
async def list_tags(
    ws_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)
    return db.query(CloudTag).filter(CloudTag.workspace_id == ws_id).all()


@router.delete("/api/workspaces/{ws_id}/tags/{tag_id}", status_code=204)
async def delete_tag(
    ws_id: str,
    tag_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "member", db)
    tag = db.query(CloudTag).filter(
        CloudTag.id == tag_id, CloudTag.workspace_id == ws_id
    ).first()
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    _record_change(ws_id, "tag", tag.id, "delete", {}, None, db)
    db.delete(tag)
    db.commit()
