"""
认证相关路由
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from backend.app.database import get_db
from backend.app.models import User
from backend.app.api.schemas import LoginRequest, Token
from backend.app.api.deps import get_current_user
from backend.app.core.security import verify_password, create_access_token
from backend.app.config import ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter(tags=["认证"])


@router.post("/login", response_model=Token)
async def login(
    login_request: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    用户登录接口
    """
    # 查询用户
    user = db.query(User).filter(User.username == login_request.username).first()
    
    # 验证用户名和密码
    if not user or not verify_password(login_request.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误"
        )
    
    # 更新最后登录时间
    user.last_login = datetime.utcnow()
    db.commit()
    
    # 创建访问令牌
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username
    }


@router.get("/auth/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    获取当前登录用户信息
    """
    return {
        "id": current_user.id,
        "username": current_user.username,
        "is_admin": current_user.is_admin,
        "last_login": current_user.last_login
    }
