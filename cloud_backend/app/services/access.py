"""Document authorization. Delivery grants deliberately do not participate here."""
from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select

from app.models import CloudDocument, DocumentAccess, TeamMember, User, Workspace


def visible_document_clause(user_id):
    member = exists(select(TeamMember.user_id).select_from(TeamMember).join(
        Workspace, Workspace.team_id == TeamMember.team_id).join(User, User.id == TeamMember.user_id).where(
        Workspace.id == CloudDocument.workspace_id, TeamMember.user_id == user_id, User.is_active.is_(True)))
    explicit = exists(select(DocumentAccess.user_id).where(
        DocumentAccess.document_id == CloudDocument.id, DocumentAccess.user_id == user_id))
    return and_(member, or_(CloudDocument.visibility == 'workspace',
                           and_(CloudDocument.visibility == 'restricted', explicit)))


def document_capabilities(doc, user_id, db):
    membership = db.query(TeamMember).join(Workspace, Workspace.team_id == TeamMember.team_id).join(
        User, User.id == TeamMember.user_id).filter(Workspace.id == doc.workspace_id,
        TeamMember.user_id == user_id, User.is_active.is_(True)).first()
    access = db.get(DocumentAccess, (doc.id, user_id))
    return _capabilities(doc, user_id, membership.role if membership else None,
                         access.role if access else None)


def _capabilities(doc, user_id, member_role, role):
    public = doc.visibility == 'workspace'
    read = bool(member_role and (public or role in ('reader', 'contributor', 'manager')))
    writable = bool(read and member_role in ('member', 'admin', 'owner'))
    manager = bool(writable and (role == 'manager' or
                   (public and (doc.created_by == user_id or member_role in ('admin', 'owner')))))
    return dict(read=read, contribute=bool(writable and (public or role in ('contributor', 'manager'))),
                manage=manager, distribute=manager,
                delete=bool(writable and public and member_role in ('admin', 'owner')))


def batch_document_capabilities(documents, user_id, db):
    """一次列表请求内复用角色与 ACL；不跨请求或写事务缓存授权。"""
    if not documents:
        return {}
    workspace_ids = {doc.workspace_id for doc in documents}
    memberships = dict(db.query(Workspace.id, TeamMember.role).join(
        TeamMember, TeamMember.team_id == Workspace.team_id).join(
        User, User.id == TeamMember.user_id).filter(
        Workspace.id.in_(workspace_ids), TeamMember.user_id == user_id,
        User.is_active.is_(True)).all())
    document_ids = [doc.id for doc in documents]
    grants = {}
    # 完整列表仍兼容旧接口；分块避免其资料数突破 SQLite 参数上限。
    for offset in range(0, len(document_ids), 500):
        grants.update(db.query(DocumentAccess.document_id, DocumentAccess.role).filter(
            DocumentAccess.user_id == user_id,
            DocumentAccess.document_id.in_(document_ids[offset:offset + 500])).all())
    return {doc.id: _capabilities(doc, user_id, memberships.get(doc.workspace_id), grants.get(doc.id))
            for doc in documents}


def require_document_access(ws_id, doc_id, user_id, db, capability='read', *, include_trashed=False):
    doc = db.query(CloudDocument).filter(CloudDocument.id == doc_id,
                                       CloudDocument.workspace_id == ws_id).first()
    if not doc or (doc.trashed_at is not None and not include_trashed):
        raise HTTPException(404, '资源不存在')
    capabilities = document_capabilities(doc, user_id, db)
    if not capabilities['read']:
        raise HTTPException(404, '资源不存在')
    if not capabilities.get(capability, False):
        raise HTTPException(403, '无此资料的操作权限')
    return doc


def validate_access_entries(entries, ws_id, db, *, creator_id=None):
    """Validate a complete grant set before writing any ACL or document state."""
    entries = [entry.model_dump() if hasattr(entry, 'model_dump') else dict(entry) for entry in entries]
    grants = {}
    for entry in entries:
        uid, role = entry['user_id'], entry.get('role', 'reader')
        if uid in grants or role not in ('reader', 'contributor', 'manager'):
            raise HTTPException(422, '授权成员重复或角色无效')
        grants[uid] = role
    if creator_id:
        grants[creator_id] = 'manager'
    memberships = db.query(TeamMember).join(Workspace, Workspace.team_id == TeamMember.team_id).join(
        User, User.id == TeamMember.user_id).filter(Workspace.id == ws_id,
        TeamMember.user_id.in_(list(grants)), User.is_active.is_(True)).all()
    roles = {m.user_id: m.role for m in memberships}
    if set(roles) != set(grants):
        raise HTTPException(422, '授权成员必须是当前组织的有效成员')
    if not any(role == 'manager' and roles[uid] in ('member', 'admin', 'owner')
               for uid, role in grants.items()):
        raise HTTPException(409, '至少保留一名有效资料管理者')
    return grants


def replace_access(doc, grants, actor_id, db):
    existing = {entry.user_id: entry for entry in db.query(DocumentAccess).filter_by(document_id=doc.id)}
    for uid, entry in existing.items():
        if uid not in grants:
            db.delete(entry)
    for uid, role in grants.items():
        if uid in existing:
            existing[uid].role = role
            existing[uid].granted_by = actor_id
        else:
            db.add(DocumentAccess(document_id=doc.id, user_id=uid, role=role, granted_by=actor_id))
