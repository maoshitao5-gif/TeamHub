"""团队与成员相关 Pydantic DTO"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z0-9\-]+$")


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)


class TeamResponse(BaseModel):
    id: str
    name: str
    slug: str
    storage_quota: int
    storage_used: int
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamDiscoverResponse(BaseModel):
    """团队发现列表项"""
    id: str
    name: str
    slug: str
    member_count: int = 0
    workspace_count: int = 0
    created_at: datetime


class MemberResponse(BaseModel):
    user_id: str
    role: str
    joined_at: datetime
    display_name: str
    email: str

    model_config = {"from_attributes": True}


class MemberInvite(BaseModel):
    email: str
    role: str = "member"


class MemberRoleUpdate(BaseModel):
    role: str  # owner / admin / member / viewer


# ========== 加入申请 ==========

class JoinRequestCreate(BaseModel):
    team_id: str
    message: Optional[str] = Field(None, max_length=500)


class JoinRequestReview(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")


class JoinRequestResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    status: str
    message: Optional[str] = None
    user_display_name: str = ""
    user_email: str = ""
    team_name: str = ""
    created_at: datetime
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
