"""
安全工具模块
JWT 生成/验证 + bcrypt 密码哈希
"""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# bcrypt 上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """生成 JWT access token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode["exp"] = expire
    to_encode["type"] = "access"
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token_value() -> str:
    """生成 refresh token 原始值（随机 UUID 字符串）"""
    import uuid
    return str(uuid.uuid4())


def hash_refresh_token(token: str) -> str:
    """对 refresh token 进行 SHA-256 哈希（用于数据库存储）"""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """
    解码并验证 access token
    返回 payload dict，验证失败返回 None
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None
