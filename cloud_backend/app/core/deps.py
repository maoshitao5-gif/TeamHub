"""
FastAPI 依赖注入
权限检查：get_current_user, check_workspace_access
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db
from app.models import User, TeamMember, Workspace, RefreshToken
from app.core.security import decode_access_token

security = HTTPBearer()

# 角色等级（数值越大权限越高）
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """从 Bearer Token 中解析当前用户"""
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的认证令牌")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已被禁用")
    if payload.get('sid'):
        session = db.query(RefreshToken.id).filter(
            RefreshToken.user_id == user.id, RefreshToken.device_id == payload['sid'],
            RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > datetime.now(timezone.utc),
        ).first()
        if not session:
            raise HTTPException(status_code=401, detail="网页会话已失效")
    return user


def check_workspace_access(ws_id: str, user_id: str, min_role: str, db: Session) -> Workspace:
    """
    检查用户对工作空间的访问权限
    min_role: viewer < member < admin < owner
    返回 Workspace 对象，权限不足时抛出 HTTPException
    """
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    if not db.query(User.id).filter(User.id == user_id, User.is_active.is_(True)).first():
        raise HTTPException(status_code=404, detail="资源不存在")
    m = db.query(TeamMember).filter(
        TeamMember.team_id == ws.team_id, TeamMember.user_id == user_id
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="资源不存在")
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK.get(min_role, 0):
        raise HTTPException(status_code=403, detail="权限不足")
    return ws


async def get_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """仅超级管理员可访问，否则返回 403"""
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限")
    return current_user


def get_workspace_document(ws_id: str, doc_id: str, db: Session, *, user_id: str,
                           capability: str = 'read', include_trashed: bool = False):
    """
    取文档并校验其归属当前工作空间(契约 §4:对无权限者不区分 404,防探测)。
    资料授权同时校验当前成员身份。
    """
    from app.services.access import require_document_access
    return require_document_access(ws_id, doc_id, user_id, db, capability,
                                   include_trashed=include_trashed)
