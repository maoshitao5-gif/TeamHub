"""工作空间相关 Pydantic DTO"""
from datetime import datetime
from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None


class WorkspaceResponse(BaseModel):
    id: str
    team_id: str
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
