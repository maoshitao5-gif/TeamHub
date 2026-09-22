"""
工作空间 API
POST   /api/teams/{team_id}/workspaces
GET    /api/teams/{team_id}/workspaces
GET    /api/workspaces/{ws_id}
PUT    /api/workspaces/{ws_id}
DELETE /api/workspaces/{ws_id}
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import User, Team, TeamMember, Workspace, CloudChangeLog, Device
from app.core.deps import get_current_user, check_workspace_access, ROLE_RANK
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate, WorkspaceResponse

router = APIRouter(tags=["工作空间"])


def _check_team_access(team_id: str, user_id: str, min_role: str, db: Session) -> TeamMember:
    m = db.query(TeamMember).filter(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id
    ).first()
    if not m:
        raise HTTPException(status_code=403, detail="您不是该团队成员")
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK.get(min_role, 0):
        raise HTTPException(status_code=403, detail="权限不足")
    return m


def _get_ws_or_404(ws_id: str, db: Session) -> Workspace:
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return ws


@router.post("/api/teams/{team_id}/workspaces", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    team_id: str,
    body: WorkspaceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_team_access(team_id, current_user.id, "admin", db)
    ws = Workspace(team_id=team_id, name=body.name, description=body.description)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


@router.get("/api/teams/{team_id}/workspaces", response_model=List[WorkspaceResponse])
async def list_workspaces(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_team_access(team_id, current_user.id, "viewer", db)
    return db.query(Workspace).filter(Workspace.team_id == team_id).all()


@router.get("/api/workspaces/{ws_id}", response_model=WorkspaceResponse)
async def get_workspace(
    ws_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ws = _get_ws_or_404(ws_id, db)
    _check_team_access(ws.team_id, current_user.id, "viewer", db)
    return ws


@router.put("/api/workspaces/{ws_id}", response_model=WorkspaceResponse)
async def update_workspace(
    ws_id: str,
    body: WorkspaceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ws = _get_ws_or_404(ws_id, db)
    _check_team_access(ws.team_id, current_user.id, "admin", db)
    if body.name is not None:
        ws.name = body.name
    if body.description is not None:
        ws.description = body.description
    db.commit()
    db.refresh(ws)
    return ws


@router.delete("/api/workspaces/{ws_id}", status_code=204)
async def delete_workspace(
    ws_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ws = _get_ws_or_404(ws_id, db)
    _check_team_access(ws.team_id, current_user.id, "admin", db)
    db.delete(ws)
    db.commit()


# ========== 活动流 ==========

@router.get("/api/workspaces/{ws_id}/activity")
async def workspace_activity(
    ws_id: str,
    limit: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询工作空间最近 N 条变更动态"""
    check_workspace_access(ws_id, current_user.id, "viewer", db)

    logs = (
        db.query(CloudChangeLog)
        .filter(CloudChangeLog.workspace_id == ws_id)
        .order_by(CloudChangeLog.created_at.desc())
        .limit(limit)
        .all()
    )

    # 批量查找 device_id → user display_name
    device_ids = {log.device_id for log in logs if log.device_id}
    device_user_map = {}
    if device_ids:
        devices = db.query(Device).filter(Device.device_id.in_(device_ids)).all()
        for d in devices:
            user = db.query(User.display_name).filter(User.id == d.user_id).first()
            if user:
                device_user_map[d.device_id] = user.display_name

    result = []
    for log in logs:
        result.append({
            "id": log.id,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "operation": log.operation,
            "payload": log.payload or {},
            "device_id": log.device_id,
            "user_display_name": device_user_map.get(log.device_id),
            "created_at": log.created_at.isoformat(),
        })
    return result
