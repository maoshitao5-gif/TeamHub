"""同源网页会话：内存访问令牌 + HttpOnly 刷新 Cookie。"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.auth import authenticate_user
from app.config import settings
from app.core.security import create_access_token, create_refresh_token_value, hash_refresh_token
from app.database import get_db
from app.models import RefreshToken, User
from app.schemas.auth import LoginRequest
from app.schemas.user import UserResponse

COOKIE_NAME = "teamhub_web_refresh"
COOKIE_PATH = "/api/web-auth"
router = APIRouter(prefix=COOKIE_PATH, tags=["网页会话"])


def require_same_origin(request: Request) -> None:
    """校验浏览器来源和自定义请求头，登录也防 CSRF。"""
    origin = urlsplit(request.headers.get("origin", ""))
    expected = urlsplit(str(request.base_url))
    if (request.headers.get("x-teamhub-web") != "1"
            or request.headers.get("sec-fetch-site") == "cross-site"
            or origin.scheme != expected.scheme or origin.netloc != expected.netloc
            or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise HTTPException(403, "网页会话仅接受同源请求")
    if request.url.scheme != "https" and request.url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(400, "网页会话需要 HTTPS；HTTP 仅允许本机开发")


def _cookie(response: Response, request: Request, raw: str) -> None:
    response.set_cookie(
        COOKIE_NAME, raw, max_age=settings.jwt_refresh_token_expire_days * 86400,
        path=COOKIE_PATH, httponly=True, secure=request.url.scheme == "https", samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def _revoke_family(db: Session, raw: str | None) -> None:
    if not raw:
        return
    old = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh_token(raw)).first()
    if old and old.device_id and old.device_id.startswith("web:"):
        db.query(RefreshToken).filter(
            RefreshToken.user_id == old.user_id, RefreshToken.device_id == old.device_id,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)


def _issue(db: Session, user: User, session_id: str) -> tuple[str, dict]:
    raw = create_refresh_token_value()
    db.add(RefreshToken(
        user_id=user.id, token_hash=hash_refresh_token(raw), device_id=session_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days),
    ))
    return raw, {
        "access_token": create_access_token({"sub": user.id, "sid": session_id}),
        "token_type": "bearer", "user": UserResponse.model_validate(user).model_dump(mode="json"),
    }


@router.post("/login", dependencies=[Depends(require_same_origin)])
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = authenticate_user(body, db, request)
    _revoke_family(db, request.cookies.get(COOKIE_NAME))
    raw, result = _issue(db, user, "web:" + uuid4().hex)
    db.commit()
    _cookie(response, request, raw)
    return result


@router.post("/refresh", dependencies=[Depends(require_same_origin)])
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(401, "请登录")
    now = datetime.now(timezone.utc)
    old = db.query(RefreshToken).filter(
        RefreshToken.token_hash == hash_refresh_token(raw), RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > now, RefreshToken.device_id.like("web:%"),
    ).first()
    if not old:
        raise HTTPException(401, "网页会话已过期，请重新登录")
    user = db.query(User).filter(User.id == old.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(401, "网页会话已失效")
    # 条件更新保证并发刷新只有一个请求能消费旧 Cookie；轮换与签发同事务提交。
    changed = db.query(RefreshToken).filter(
        RefreshToken.id == old.id, RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now,
    ).update({"revoked_at": now}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise HTTPException(401, "网页会话已轮换，请重试")
    new_raw, result = _issue(db, user, old.device_id)
    db.commit()
    _cookie(response, request, new_raw)
    return result


@router.post("/logout", status_code=204, dependencies=[Depends(require_same_origin)])
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    _revoke_family(db, request.cookies.get(COOKIE_NAME))
    db.commit()
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True,
                           secure=request.url.scheme == "https", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
