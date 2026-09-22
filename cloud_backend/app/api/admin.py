"""
管理员 API（仅超级管理员可访问）
GET  /api/admin/stats          — 系统概况
GET  /api/admin/users          — 用户列表（分页+搜索）
PUT  /api/admin/users/{id}     — 更新用户
DELETE /api/admin/users/{id}   — 删除用户
GET  /api/admin/teams          — 团队列表
PUT  /api/admin/teams/{id}     — 更新团队
DELETE /api/admin/teams/{id}   — 解散团队
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models import User, Team, TeamMember, Workspace, Device, CloudChangeLog, RefreshToken
from app.core.deps import get_superuser

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ========== 请求/响应 Schema ==========

class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None


class TeamUpdate(BaseModel):
    storage_quota: Optional[int] = None  # 字节数


# ========== 工具函数 ==========

def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "is_active": u.is_active,
        "is_superuser": u.is_superuser,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _team_dict(t: Team, db: Session) -> dict:
    member_count = db.query(TeamMember).filter(TeamMember.team_id == t.id).count()
    ws_count = db.query(Workspace).filter(Workspace.team_id == t.id).count()
    return {
        "id": t.id,
        "name": t.name,
        "slug": t.slug,
        "storage_quota": t.storage_quota,
        "storage_used": t.storage_used,
        "member_count": member_count,
        "workspace_count": ws_count,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ========== 统计概况 ==========

@router.get("/stats")
async def get_stats(
    _: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """系统运营数据概况"""
    user_count = db.query(User).count()
    team_count = db.query(Team).count()
    ws_count = db.query(Workspace).count()
    device_count = db.query(Device).count()
    change_log_count = db.query(CloudChangeLog).count()
    recent_users = db.query(User).order_by(User.created_at.desc()).limit(10).all()
    return {
        "user_count": user_count,
        "team_count": team_count,
        "workspace_count": ws_count,
        "device_count": device_count,
        "change_log_count": change_log_count,
        "recent_users": [_user_dict(u) for u in recent_users],
    }


# ========== 用户管理 ==========

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: Optional[str] = Query(None),
    _: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """用户列表（分页 + 关键词搜索 email / display_name）"""
    q = db.query(User)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            (User.email.like(like)) | (User.display_name.like(like))
        )
    total = q.count()
    users = q.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_user_dict(u) for u in users],
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    current_admin: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """更新用户（is_active、is_superuser）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superuser is not None:
        user.is_superuser = body.is_superuser
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    current_admin: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """删除用户（自动撤销所有 refresh token）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == current_admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    db.delete(user)
    db.commit()


# ========== 团队管理 ==========

@router.get("/teams")
async def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """团队列表（含成员数、工作空间数、存储用量）"""
    q = db.query(Team)
    total = q.count()
    teams = q.order_by(Team.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_team_dict(t, db) for t in teams],
    }


@router.put("/teams/{team_id}")
async def update_team(
    team_id: str,
    body: TeamUpdate,
    _: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """更新团队（storage_quota）"""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")
    if body.storage_quota is not None:
        team.storage_quota = body.storage_quota
    db.commit()
    db.refresh(team)
    return _team_dict(team, db)


@router.delete("/teams/{team_id}", status_code=204)
async def delete_team(
    team_id: str,
    _: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """解散团队"""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")
    db.delete(team)
    db.commit()
