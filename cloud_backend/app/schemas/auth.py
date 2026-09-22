"""认证相关 Pydantic DTO"""
from pydantic import BaseModel, EmailStr, Field
from app.schemas.user import UserResponse


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse | None = None


class RefreshRequest(BaseModel):
    refresh_token: str
    device_id: str | None = None


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
