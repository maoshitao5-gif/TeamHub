"""
团队 API
POST   /api/teams
GET    /api/teams
GET    /api/teams/{team_id}
PUT    /api/teams/{team_id}
DELETE /api/teams/{team_id}
GET    /api/teams/{team_id}/members
POST   /api/teams/{team_id}/members        # 邀请成员（按邮箱）
PUT    /api/teams/{team_id}/members/{user_id}
DELETE /api/teams/{team_id}/members/{user_id}
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import User, Team, TeamMember, Workspace, JoinRequest
from app.core.deps import get_current_user, require_team_member, ROLE_RANK
from app.schemas.team import (
    TeamCreate, TeamUpdate, TeamResponse, TeamDiscoverResponse,
    MemberResponse, MemberInvite, MemberRoleUpdate,
    JoinRequestCreate, JoinRequestReview, JoinRequestResponse,
)

router = APIRouter(prefix="/api/teams", tags=["团队"])


def _get_team_or_404(team_id: str, db: Session) -> Team:
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")
    return team


def _get_membership(team_id: str, user_id: str, db: Session) -> TeamMember:
    m = db.query(TeamMember).filter(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id
    ).first()
    if not m:
        raise HTTPException(status_code=403, detail="您不是该团队成员")
    return m


@router.post("", response_model=TeamResponse, status_code=201)
async def create_team(
    body: TeamCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建团队，创建者自动成为 owner"""
    if db.query(Team).filter(Team.slug == body.slug).first():
        raise HTTPException(status_code=400, detail="该 slug 已被使用")

    team = Team(name=body.name, slug=body.slug)
    db.add(team)
    db.flush()

    membership = TeamMember(team_id=team.id, user_id=current_user.id, role="owner")
    db.add(membership)
    db.commit()
    db.refresh(team)
    return team


@router.get("", response_model=List[TeamResponse])
async def list_teams(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户所属的团队"""
    memberships = db.query(TeamMember).filter(TeamMember.user_id == current_user.id).all()
    team_ids = [m.team_id for m in memberships]
    return db.query(Team).filter(Team.id.in_(team_ids)).all()


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team = _get_team_or_404(team_id, db)
    _get_membership(team_id, current_user.id, db)
    return team


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    body: TeamUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team = _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    if body.name is not None:
        team.name = body.name
    db.commit()
    db.refresh(team)
    return team


@router.delete("/{team_id}", status_code=204)
async def delete_team(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team = _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)
    if m.role != "owner":
        raise HTTPException(status_code=403, detail="只有 owner 才能解散团队")
    db.delete(team)
    db.commit()


# ========== 成员管理 ==========

@router.get("/{team_id}/members", response_model=List[MemberResponse])
async def list_members(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_team_or_404(team_id, db)
    _get_membership(team_id, current_user.id, db)

    memberships = db.query(TeamMember).filter(TeamMember.team_id == team_id).all()
    result = []
    for m in memberships:
        result.append(MemberResponse(
            user_id=m.user_id,
            role=m.role,
            joined_at=m.joined_at,
            display_name=m.user.display_name,
            email=m.user.email,
        ))
    return result


@router.post("/{team_id}/members", response_model=MemberResponse, status_code=201)
async def invite_member(
    team_id: str,
    body: MemberInvite,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通过邮箱邀请用户加入团队"""
    _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限才能邀请成员")

    target = db.query(User).filter(User.email == body.email).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在，请先注册")

    existing = db.query(TeamMember).filter(
        TeamMember.team_id == team_id, TeamMember.user_id == target.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该用户已是团队成员")

    new_member = TeamMember(team_id=team_id, user_id=target.id, role=body.role)
    db.add(new_member)
    db.commit()
    db.refresh(new_member)

    return MemberResponse(
        user_id=new_member.user_id,
        role=new_member.role,
        joined_at=new_member.joined_at,
        display_name=target.display_name,
        email=target.email,
    )


@router.put("/{team_id}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    team_id: str,
    user_id: str,
    body: MemberRoleUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改成员角色"""
    _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    target_m = db.query(TeamMember).filter(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id
    ).first()
    if not target_m:
        raise HTTPException(status_code=404, detail="成员不存在")

    target_m.role = body.role
    db.commit()
    db.refresh(target_m)

    return MemberResponse(
        user_id=target_m.user_id,
        role=target_m.role,
        joined_at=target_m.joined_at,
        display_name=target_m.user.display_name,
        email=target_m.user.email,
    )


@router.delete("/{team_id}/members/{user_id}", status_code=204)
async def remove_member(
    team_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """移除成员（或自己退出）"""
    _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)

    # 自己退出，或有管理员权限
    is_self = user_id == current_user.id
    if not is_self and ROLE_RANK.get(m.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    target_m = db.query(TeamMember).filter(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id
    ).first()
    if not target_m:
        raise HTTPException(status_code=404, detail="成员不存在")
    if target_m.role == "owner":
        raise HTTPException(status_code=400, detail="不能移除 owner，请先转让所有权")

    db.delete(target_m)
    db.commit()


# ========== 团队发现 ==========

@router.get("/discover", response_model=List[TeamDiscoverResponse])
async def discover_teams(
    q: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """公开浏览所有团队，支持关键词搜索"""
    query = db.query(Team)
    if q.strip():
        like_pattern = f"%{q.strip()}%"
        query = query.filter(
            (Team.name.ilike(like_pattern)) | (Team.slug.ilike(like_pattern))
        )
    teams = query.order_by(Team.created_at.desc()).limit(50).all()

    result = []
    for team in teams:
        member_count = db.query(func.count(TeamMember.user_id)).filter(
            TeamMember.team_id == team.id
        ).scalar()
        workspace_count = db.query(func.count(Workspace.id)).filter(
            Workspace.team_id == team.id
        ).scalar()
        result.append(TeamDiscoverResponse(
            id=team.id,
            name=team.name,
            slug=team.slug,
            member_count=member_count,
            workspace_count=workspace_count,
            created_at=team.created_at,
        ))
    return result


# ========== 加入申请 ==========

@router.post("/join-requests", response_model=JoinRequestResponse, status_code=201)
async def create_join_request(
    body: JoinRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交加入团队申请"""
    team = _get_team_or_404(body.team_id, db)

    # 已是成员则跳过
    existing_member = db.query(TeamMember).filter(
        TeamMember.team_id == body.team_id,
        TeamMember.user_id == current_user.id,
    ).first()
    if existing_member:
        raise HTTPException(status_code=400, detail="您已是该团队成员")

    # 已有待审申请则不重复创建
    existing_req = db.query(JoinRequest).filter(
        JoinRequest.team_id == body.team_id,
        JoinRequest.user_id == current_user.id,
        JoinRequest.status == "pending",
    ).first()
    if existing_req:
        raise HTTPException(status_code=400, detail="您已提交过申请，请等待审批")

    req = JoinRequest(
        team_id=body.team_id,
        user_id=current_user.id,
        message=body.message,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    return JoinRequestResponse(
        id=req.id,
        team_id=req.team_id,
        user_id=req.user_id,
        status=req.status,
        message=req.message,
        user_display_name=current_user.display_name,
        user_email=current_user.email,
        team_name=team.name,
        created_at=req.created_at,
    )


@router.get("/join-requests/my", response_model=List[JoinRequestResponse])
async def my_join_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查看自己的所有申请"""
    reqs = db.query(JoinRequest).filter(
        JoinRequest.user_id == current_user.id
    ).order_by(JoinRequest.created_at.desc()).all()

    result = []
    for r in reqs:
        result.append(JoinRequestResponse(
            id=r.id,
            team_id=r.team_id,
            user_id=r.user_id,
            status=r.status,
            message=r.message,
            user_display_name=current_user.display_name,
            user_email=current_user.email,
            team_name=r.team.name,
            created_at=r.created_at,
            reviewed_by=r.reviewed_by,
            reviewed_at=r.reviewed_at,
        ))
    return result


@router.get("/join-requests/pending-count")
async def pending_join_request_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """管理员用：当前用户管理的所有团队待审总数"""
    # 找出用户作为 admin/owner 的团队
    admin_teams = db.query(TeamMember.team_id).filter(
        TeamMember.user_id == current_user.id,
        TeamMember.role.in_(["admin", "owner"]),
    ).all()
    team_ids = [t.team_id for t in admin_teams]
    if not team_ids:
        return {"count": 0}

    count = db.query(func.count(JoinRequest.id)).filter(
        JoinRequest.team_id.in_(team_ids),
        JoinRequest.status == "pending",
    ).scalar()
    return {"count": count}


@router.get("/{team_id}/join-requests", response_model=List[JoinRequestResponse])
async def list_team_join_requests(
    team_id: str,
    status: str = "pending",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """管理员查看该团队的申请列表"""
    _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    query = db.query(JoinRequest).filter(JoinRequest.team_id == team_id)
    if status:
        query = query.filter(JoinRequest.status == status)
    reqs = query.order_by(JoinRequest.created_at.desc()).all()

    result = []
    for r in reqs:
        result.append(JoinRequestResponse(
            id=r.id,
            team_id=r.team_id,
            user_id=r.user_id,
            status=r.status,
            message=r.message,
            user_display_name=r.user.display_name,
            user_email=r.user.email,
            team_name=r.team.name,
            created_at=r.created_at,
            reviewed_by=r.reviewed_by,
            reviewed_at=r.reviewed_at,
        ))
    return result


@router.post("/{team_id}/join-requests/{request_id}/review", response_model=JoinRequestResponse)
async def review_join_request(
    team_id: str,
    request_id: str,
    body: JoinRequestReview,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """审批加入申请"""
    _get_team_or_404(team_id, db)
    m = _get_membership(team_id, current_user.id, db)
    if ROLE_RANK.get(m.role, 0) < ROLE_RANK["admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    req = db.query(JoinRequest).filter(
        JoinRequest.id == request_id,
        JoinRequest.team_id == team_id,
    ).first()
    if not req:
        raise HTTPException(status_code=404, detail="申请不存在")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="该申请已被处理")

    now = datetime.now(timezone.utc)
    req.status = "approved" if body.action == "approve" else "rejected"
    req.reviewed_by = current_user.id
    req.reviewed_at = now

    # 通过后自动加入团队
    if body.action == "approve":
        existing = db.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == req.user_id,
        ).first()
        if not existing:
            db.add(TeamMember(team_id=team_id, user_id=req.user_id, role="member"))

    db.commit()
    db.refresh(req)

    return JoinRequestResponse(
        id=req.id,
        team_id=req.team_id,
        user_id=req.user_id,
        status=req.status,
        message=req.message,
        user_display_name=req.user.display_name,
        user_email=req.user.email,
        team_name=req.team.name,
        created_at=req.created_at,
        reviewed_by=req.reviewed_by,
        reviewed_at=req.reviewed_at,
    )
