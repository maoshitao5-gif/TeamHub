"""
认证 API
POST /api/auth/register
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, RefreshToken
from app.core.security import (
    hash_password, verify_password,
    create_access_token,
    create_refresh_token_value, hash_refresh_token,
)
from app.schemas.auth import (
    RegisterRequest, LoginRequest,
    TokenResponse, RefreshRequest, AccessTokenResponse,
)
from app.schemas.user import UserResponse
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _issue_tokens(user: User, device_id: str | None, db: Session) -> TokenResponse:
    """为用户签发 access token + refresh token，并将 refresh token 存库"""
    access_token = create_access_token({"sub": user.id})

    raw_refresh = create_refresh_token_value()
    token_hash = hash_refresh_token(raw_refresh)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)

    db_token = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        device_id=device_id,
        expires_at=expires_at,
    )
    db.add(db_token)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=UserResponse.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """注册新用户，返回 access_token + refresh_token"""
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="该邮箱已被注册")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return _issue_tokens(user, device_id=None, db=db)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    """登录，返回 access_token + refresh_token"""
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    return _issue_tokens(user, device_id=None, db=db)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """
    用 refresh_token 换取新 access_token（静默续期）
    同时撤销旧 refresh_token，签发新 refresh_token（Token Rotation）
    """
    token_hash = hash_refresh_token(body.refresh_token)
    db_token = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.revoked_at == None,
    ).first()

    if not db_token:
        raise HTTPException(status_code=401, detail="无效或已撤销的刷新令牌")
    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="刷新令牌已过期，请重新登录")

    user = db.query(User).filter(User.id == db_token.user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")

    # 撤销旧 token（Token Rotation 防止重放攻击）
    db_token.revoked_at = datetime.now(timezone.utc)
    db.commit()

    # 签发新 access token（不签发新 refresh token，由客户端决定何时再次 refresh）
    new_access = create_access_token({"sub": user.id})
    return AccessTokenResponse(access_token=new_access)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: Session = Depends(get_db)):
    """撤销当前设备的 refresh_token（登出）"""
    token_hash = hash_refresh_token(body.refresh_token)
    db_token = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if db_token and not db_token.revoked_at:
        db_token.revoked_at = datetime.now(timezone.utc)
        db.commit()
