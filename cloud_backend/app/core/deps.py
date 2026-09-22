"""
FastAPI 依赖注入
权限检查：get_current_user, require_team_member, check_workspace_access
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, TeamMember, Workspace, CloudChangeLog
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
    m = db.query(TeamMember).filter(
        TeamMember.team_id == ws.team_id, TeamMember.user_id == user_id
    ).first()
    if not m:
        raise HTTPException(status_code=403, detail="无权限访问该工作空间")
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK.get(min_role, 0):
        raise HTTPException(status_code=403, detail="权限不足")
    return ws


def next_sequence_number(ws_id: str, db: Session) -> int:
    """获取工作空间下一个变更日志序列号（单调递增）"""
    last = db.query(CloudChangeLog).filter(
        CloudChangeLog.workspace_id == ws_id
    ).order_by(CloudChangeLog.sequence_number.desc()).first()
    return (last.sequence_number + 1) if last else 1


async def get_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """仅超级管理员可访问，否则返回 403"""
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限")
    return current_user


def require_team_member(min_role: str = "viewer"):
    """
    工厂函数：返回检查用户是否是指定团队成员的依赖
    min_role: viewer < member < admin < owner
    """
    async def _check(
        team_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> TeamMember:
        membership = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == current_user.id,
        ).first()
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="您不是该团队成员")
        if ROLE_RANK.get(membership.role, 0) < ROLE_RANK.get(min_role, 0):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return membership

    return _check
