"""
API 依赖注入模块
包含认证、权限检查等依赖
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User
from backend.app.core.security import verify_token

# HTTP Bearer Token 安全方案
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    获取当前登录用户
    从请求头中提取 token 并验证
    """
    token = credentials.credentials
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=401,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_admin_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    检查当前用户是否为超级管理员
    只有超级管理员才能访问管理后台
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="权限不足，需要超级管理员权限"
        )
    return current_user
