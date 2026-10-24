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
from datetime import datetime, timezone
from sqlalchemy import func, text
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models import User, Team, TeamMember, Workspace, Device, CloudChangeLog, RefreshToken, AdminAction, CloudDocument, Delivery
from app.core.deps import get_superuser
from app.services.storage_usage import team_storage_usage

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


def _team_dicts(teams: list[Team], db: Session) -> list[dict]:
    if not teams:
        return []
    ids = [team.id for team in teams]
    member_counts = dict(db.query(TeamMember.team_id, func.count(TeamMember.user_id)).filter(
        TeamMember.team_id.in_(ids)).group_by(TeamMember.team_id).all())
    workspace_counts = dict(db.query(Workspace.team_id, func.count(Workspace.id)).filter(
        Workspace.team_id.in_(ids)).group_by(Workspace.team_id).all())
    usage = team_storage_usage(db, ids)
    return [{
        "id": t.id,
        "name": t.name,
        "slug": t.slug,
        "storage_quota": t.storage_quota,
        "storage_used": usage[t.id],
        "member_count": member_counts.get(t.id, 0),
        "workspace_count": workspace_counts.get(t.id, 0),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in teams]


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


def _change_user_state(user_id, body, current_admin, db):
    actor_id = current_admin.id
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    actor = db.get(User, actor_id)
    if not actor or not actor.is_active or not actor.is_superuser:
        raise HTTPException(403, "管理员权限已失效")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    disabling = body.is_active is False or body.is_superuser is False
    if user.id == actor_id and disabling:
        raise HTTPException(400, "不能停用自己或撤销自己的系统管理员权限")
    if disabling and user.is_active and user.is_superuser:
        active_admins = db.query(User).filter(User.is_active.is_(True), User.is_superuser.is_(True)).count()
        if active_admins <= 1:
            raise HTTPException(409, "必须保留至少一名可用系统管理员")
    changes = {}
    for key in ('is_active', 'is_superuser'):
        value = getattr(body, key)
        if value is not None and value != getattr(user, key):
            changes[key] = value
            setattr(user, key, value)
    if body.is_active is False:
        db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).update(
            {'revoked_at': datetime.now(timezone.utc)}, synchronize_session=False)
    if changes:
        db.add(AdminAction(actor_id=actor_id, action='deactivate_user' if body.is_active is False else 'update_user',
                           target={'user_id': user.id, 'changes': changes}))
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, current_admin: User = Depends(get_superuser), db: Session = Depends(get_db)):
    return _user_dict(_change_user_state(user_id, body, current_admin, db))


@router.delete("/users/{user_id}")
def delete_user(user_id: str, current_admin: User = Depends(get_superuser)):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=410, content={'error': {'code': 'GONE_DECOMMISSIONED',
                        'message': '账号物理删除已停用，请停用账号以保留组织资料和贡献记录'}})


@router.post("/users/{user_id}/deactivate")
def deactivate_user(user_id: str, current_admin: User = Depends(get_superuser), db: Session = Depends(get_db)):
    user = _change_user_state(user_id, UserUpdate(is_active=False), current_admin, db)
    return {'message': '成员已停用，资料和贡献保留', 'user_id': user.id}


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
        "items": _team_dicts(teams, db),
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
    return _team_dicts([team], db)[0]


@router.delete("/teams/{team_id}", status_code=204)
async def delete_team(
    team_id: str,
    _: User = Depends(get_superuser),
    db: Session = Depends(get_db),
):
    """解散团队"""
    db.rollback()
    db.execute(text('BEGIN IMMEDIATE'))
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")
    if (db.query(CloudDocument.id).join(Workspace).filter(Workspace.team_id == team_id).first()
            or db.query(Delivery.id).filter_by(team_id=team_id).first()):
        raise HTTPException(409, '团队仍有组织资料或投递记录，不能直接解散')
    db.delete(team)
    db.commit()
