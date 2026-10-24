"""
共享标签协作 / 回收站 / 联想 API(M5,契约 §3.4 / §5.2)

- 标签按关联增删(不整组替换他人贡献):add=标签名,remove=关联 tag_id;
  普通成员只能移除自己新增的关联,管理员可移除任意(§3.4);
- 回收站 trash/restore/purge 仅组织管理员,purge 前检查内容引用(不误删仍被引用对象);
- 名称/标签/共现联想,均限定当前空间(§5.2),候选数受限。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, text, or_, and_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User, CloudDocument, CloudVersion, CloudTag, TeamMember, AdminAction, DeliveryItem,
    cloud_document_tags,
)
from app.core.deps import get_current_user, check_workspace_access, get_workspace_document
from app.core import object_store
from app.api.publications import _begin_write
from app.services.access import visible_document_clause, document_capabilities
from app.schemas.collaboration import TagLinkUpdate, TagLinkItem

router = APIRouter(tags=["协作"])


def _err(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _is_admin(ws, user_id: str, db: Session) -> bool:
    membership = db.query(TeamMember).filter(
        TeamMember.team_id == ws.team_id, TeamMember.user_id == user_id
    ).first()
    return bool(membership and membership.role in ("admin", "owner"))


def _list_links(db: Session, doc_id: str, user_id: str, is_admin: bool, can_write: bool = True) -> list:
    rows = db.execute(
        cloud_document_tags.select().where(cloud_document_tags.c.document_id == doc_id)
    ).fetchall()
    result = []
    for row in rows:
        tag = db.get(CloudTag, row.tag_id)
        author = db.get(User, row.added_by) if row.added_by else None
        result.append(TagLinkItem(
            tag_id=row.tag_id,
            name=tag.name if tag else "",
            added_by=row.added_by,
            created_at=row.created_at,
            contributor_name=author.display_name if author else None,
            can_remove=can_write and (is_admin or row.added_by == user_id),
        ).model_dump(mode="json"))
    return result


# ========== 标签关联增删 ==========

@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/tags")
def update_document_tags(
    ws_id: str,
    doc_id: str,
    body: TagLinkUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _begin_write(db, ws_id, current_user)
    ws = check_workspace_access(ws_id, current_user.id, "member", db)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='contribute')
    is_admin = (_is_admin(ws, current_user.id, db) if doc.visibility == 'workspace'
                else document_capabilities(doc, current_user.id, db)['manage'])

    for tag_id in set(body.remove):
        row = db.execute(cloud_document_tags.select().where(
            (cloud_document_tags.c.document_id == doc.id) & (cloud_document_tags.c.tag_id == tag_id)
        )).first()
        if row and not is_admin and row.added_by != current_user.id:
            return _err(403, "FORBIDDEN", "只能移除自己新增的标签关联")

    # 新增关联(幂等):同名标签在空间内复用,贡献者记当前用户
    for name in body.add:
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
        exists = db.execute(cloud_document_tags.select().where(
            (cloud_document_tags.c.document_id == doc.id) &
            (cloud_document_tags.c.tag_id == tag.id)
        )).first()
        if exists:
            continue
        db.execute(cloud_document_tags.insert().values(
            document_id=doc.id, tag_id=tag.id, added_by=current_user.id,
            created_at=datetime.now(timezone.utc),
        ))

    # 移除关联:普通成员只能移除自己新增的,管理员可移除任意
    for tag_id in body.remove:
        row = db.execute(cloud_document_tags.select().where(
            (cloud_document_tags.c.document_id == doc.id) &
            (cloud_document_tags.c.tag_id == tag_id)
        )).first()
        if not row:
            continue
        if not is_admin and row.added_by != current_user.id:
            return _err(403, "FORBIDDEN", "只能移除自己新增的标签关联")
        db.execute(cloud_document_tags.delete().where(
            (cloud_document_tags.c.document_id == doc.id) &
            (cloud_document_tags.c.tag_id == tag_id)
        ))

    db.commit()
    return _list_links(db, doc.id, current_user.id, is_admin)


# ========== 共享回收站(仅管理员) ==========

@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/trash")
def trash_document(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _begin_write(db, ws_id, current_user)
    check_workspace_access(ws_id, current_user.id, "admin", db)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='delete', include_trashed=True)
    if doc.trashed_at is not None:
        return {"message": "已在共享回收站"}
    doc.trashed_at = datetime.now(timezone.utc)
    doc.trashed_by = current_user.id
    db.add(AdminAction(actor_id=current_user.id, action="trash",
                       target={"workspace_id": ws_id, "document_id": doc.id, "name": doc.name}))
    db.commit()
    return {"message": "已移入共享回收站"}


@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/restore")
def restore_document(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _begin_write(db, ws_id, current_user)
    check_workspace_access(ws_id, current_user.id, "admin", db)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='delete', include_trashed=True)
    if doc.trashed_at is None:
        return {"message": "资料已处于正常状态"}
    doc.trashed_at = None
    doc.trashed_by = None
    db.add(AdminAction(actor_id=current_user.id, action="restore",
                       target={"workspace_id": ws_id, "document_id": doc.id, "name": doc.name}))
    db.commit()
    return {"message": "已恢复"}


@router.post("/api/workspaces/{ws_id}/documents/{doc_id}/purge")
def purge_document(
    ws_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _begin_write(db, ws_id, current_user)
    check_workspace_access(ws_id, current_user.id, "admin", db)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id, capability='delete', include_trashed=True)

    if doc.trashed_at is None:
        return _err(409, "TRASH_REQUIRED", "请先移入共享回收站，再明确永久删除")
    versions = db.query(CloudVersion).filter(CloudVersion.document_id == doc.id).all()
    hashes = {v.manifest_hash or v.content_hash for v in versions if v.manifest_hash or v.content_hash}
    db.add(AdminAction(actor_id=current_user.id, action="purge",
                       target={"workspace_id": ws_id, "document_id": doc.id, "name": doc.name}))
    db.delete(doc)
    db.commit()  # 元数据先成功删除；失败时绝不能动内容对象。
    deleted, retained = [], []
    try:
        # 与发布共用 SQLite 写锁，复查所有空间、全部版本（包括回收站）的真实对象引用。
        db.execute(text("BEGIN IMMEDIATE"))
        for digest in hashes:
            try:
                key = object_store.storage_key_for(digest)
                reference = db.query(CloudVersion.id).filter(or_(
                    CloudVersion.storage_key == key,
                    CloudVersion.manifest_hash == digest,
                    and_(CloudVersion.manifest_hash.is_(None), CloudVersion.content_hash == digest),
                )).first()
                delivery_reference = db.query(DeliveryItem.id).filter(or_(
                    DeliveryItem.storage_key == key, DeliveryItem.manifest_hash == digest,
                    and_(DeliveryItem.manifest_hash.is_(None), DeliveryItem.content_hash == digest),
                )).first()
                if reference or delivery_reference:
                    retained.append(digest)
                    continue
                object_store.object_path(digest).unlink(missing_ok=True)
                deleted.append(digest)
            except (OSError, object_store.StorageValidationError):
                retained.append(digest)
        db.commit()
    except OperationalError:
        db.rollback()
        retained = list(hashes - set(deleted))
    return {"message": "资料已永久删除", "objects_deleted": len(deleted),
            "objects_retained": len(retained)}


@router.get("/api/workspaces/{ws_id}/documents/{doc_id}/tags")
def list_document_tags(ws_id: str, doc_id: str, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    ws = check_workspace_access(ws_id, current_user.id, "viewer", db)
    doc = get_workspace_document(ws_id, doc_id, db, user_id=current_user.id)
    membership = db.query(TeamMember).filter_by(team_id=ws.team_id, user_id=current_user.id).one()
    capabilities = document_capabilities(doc, current_user.id, db)
    can_remove_others = (_is_admin(ws, current_user.id, db) if doc.visibility == 'workspace'
                         else capabilities['manage'])
    return _list_links(db, doc.id, current_user.id, can_remove_others, capabilities['contribute'])


@router.get("/api/workspaces/{ws_id}/collaboration-context")
def collaboration_context(ws_id: str, current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    ws = check_workspace_access(ws_id, current_user.id, 'viewer', db)
    member = db.query(TeamMember).filter_by(team_id=ws.team_id, user_id=current_user.id).one()
    return dict(user_id=current_user.id, workspace_id=ws.id, workspace_name=ws.name,
                can_tag=member.role in ('member', 'admin', 'owner'),
                can_manage=member.role in ('admin', 'owner'),
                can_deactivate_users=current_user.is_superuser)


@router.get("/api/workspaces/{ws_id}/trash")
def list_trash(ws_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_workspace_access(ws_id, current_user.id, 'admin', db)
    query = db.query(CloudDocument).filter(CloudDocument.workspace_id == ws_id, visible_document_clause(current_user.id), CloudDocument.trashed_at.isnot(None))
    total = query.count()
    items = query.order_by(CloudDocument.trashed_at.desc(), CloudDocument.id).offset((page-1)*page_size).limit(page_size).all()
    return dict(total=total, items=[dict(id=d.id, name=d.name, trashed_at=d.trashed_at, total_size=d.total_size) for d in items])


@router.get("/api/workspaces/{ws_id}/members-for-deactivation")
def members_for_deactivation(ws_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ws = check_workspace_access(ws_id, current_user.id, 'viewer', db)
    if not current_user.is_superuser:
        raise HTTPException(403, '账号停用需系统管理员权限')
    users = db.query(User).join(TeamMember, TeamMember.user_id == User.id).filter(TeamMember.team_id == ws.team_id).all()
    return [dict(id=u.id, display_name=u.display_name, is_active=u.is_active) for u in users]


# ========== 名称/标签/共现联想(范围过滤) ==========

@router.get("/api/workspaces/{ws_id}/documents/suggestions")
def suggestions(
    ws_id: str,
    type: str = Query("name"),
    q: str = Query(""),
    selected_tag_ids: str = Query(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    check_workspace_access(ws_id, current_user.id, "viewer", db)

    if type == "name":
        docs = db.query(CloudDocument.name).filter(
            CloudDocument.workspace_id == ws_id, visible_document_clause(current_user.id),
            CloudDocument.trashed_at == None,
            CloudDocument.name.ilike(f"%{q.strip()}%"),
        ).distinct().order_by(CloudDocument.name).limit(10).all()
        return [{"name": r[0]} for r in docs]

    if type == "tag":
        tags = db.query(CloudTag).join(cloud_document_tags, CloudTag.id == cloud_document_tags.c.tag_id).join(
            CloudDocument, CloudDocument.id == cloud_document_tags.c.document_id).filter(
            CloudDocument.workspace_id == ws_id, visible_document_clause(current_user.id), CloudDocument.trashed_at.is_(None),
            CloudTag.workspace_id == ws_id,
            CloudTag.name.ilike(f"%{q.strip()}%"),
        ).distinct().order_by(CloudTag.name).limit(10).all()
        return [{"id": t.id, "name": t.name} for t in tags]

    if type == "cooccurrence":
        selected = list(dict.fromkeys(s.strip() for s in selected_tag_ids.split(",") if s.strip()))
        if not selected:
            return []
        # 含全部已选标签的文档集(AND 组合,限定当前空间)
        doc_ids_subq = (
            db.query(cloud_document_tags.c.document_id)
            .join(CloudDocument, CloudDocument.id == cloud_document_tags.c.document_id)
            .filter(
                cloud_document_tags.c.tag_id.in_(selected),
                CloudDocument.workspace_id == ws_id, visible_document_clause(current_user.id),
                CloudDocument.name.ilike(f"%{q.strip()}%"),
                CloudDocument.trashed_at == None,
            )
            .group_by(cloud_document_tags.c.document_id)
            .having(func.count(func.distinct(cloud_document_tags.c.tag_id)) == len(selected))
            .subquery()
        )
        # 统计这些文档中其他标签的共现频率
        results = (
            db.query(CloudTag, func.count(cloud_document_tags.c.document_id).label("cnt"))
            .join(cloud_document_tags, CloudTag.id == cloud_document_tags.c.tag_id)
            .filter(
                cloud_document_tags.c.document_id.in_(doc_ids_subq),
                CloudTag.workspace_id == ws_id,
                CloudTag.id.notin_(selected),
            )
            .group_by(CloudTag.id)
            .order_by(func.count(cloud_document_tags.c.document_id).desc())
            .limit(8)
            .all()
        )
        return [{"id": t.id, "name": t.name, "count": cnt} for t, cnt in results]

    return _err(400, "VALIDATION_ERROR", f"未知联想类型: {type}")
