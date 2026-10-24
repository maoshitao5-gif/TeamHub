"""
收藏 API 路由(M3,契约 §2.5 / §5.1)

local 收藏引用本地资料 ID;shared 收藏存服务端引用(真实联调留 M4-M5)。
失去访问权时由前端提示不可用,不绕过鉴权(契约 §2.5)。
"""
from typing import Literal
from urllib.parse import quote

import httpx

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Favorite, Document
from backend.app.core.logger import get_logger
from backend.app.api.schemas import FavoriteCreate, FavoriteResponse
from backend.app.services.document_responses import build_document_response, build_document_responses

logger = get_logger("api.favorites")
router = APIRouter(prefix="/api/favorites", tags=["favorites"])


def _to_response(fav: Favorite, db: Session, documents: dict | None = None) -> FavoriteResponse:
    document = None
    if fav.target_type == "local" and fav.local_document_id:
        if documents is not None:
            document = documents.get(fav.local_document_id)
        else:
            doc = db.get(Document, fav.local_document_id)
            if doc:
                document = build_document_response(doc)
    return FavoriteResponse(
        id=fav.id,
        target_type=fav.target_type,
        local_document_id=fav.local_document_id,
        server_url=fav.server_url,
        workspace_id=fav.workspace_id,
        shared_document_id=fav.shared_document_id,
        created_at=fav.created_at,
        document=document,
    )


@router.post("", response_model=FavoriteResponse, status_code=201)
def add_favorite(req: FavoriteCreate, db: Session = Depends(get_db)):
    """收藏资料(local 目标按资料 ID 引用,幂等)"""
    if req.target_type == "local":
        if not req.local_document_id:
            raise HTTPException(400, "本地收藏需提供 local_document_id")
        doc = db.get(Document, req.local_document_id)
        if not doc:
            raise HTTPException(404, "资料不存在")
        if doc.status != 'organized':
            raise HTTPException(400, "请先完成整理后再收藏资料")
        existing = db.query(Favorite).filter(
            Favorite.target_type == "local",
            Favorite.local_document_id == req.local_document_id,
        ).first()
        if existing:
            return _to_response(existing, db)
        fav = Favorite(target_type="local", local_document_id=req.local_document_id)
    else:  # shared
        if not (req.server_url and req.workspace_id and req.shared_document_id):
            raise HTTPException(400, "共享收藏需提供 server_url/workspace_id/shared_document_id")
        existing = db.query(Favorite).filter_by(
            target_type='shared', server_url=req.server_url.rstrip('/'),
            workspace_id=req.workspace_id, shared_document_id=req.shared_document_id,
        ).first()
        if existing:
            return _to_response(existing, db)
        fav = Favorite(
            target_type="shared",
            server_url=req.server_url.rstrip('/'),
            workspace_id=req.workspace_id,
            shared_document_id=req.shared_document_id,
        )

    db.add(fav)
    db.commit()
    db.refresh(fav)
    return _to_response(fav, db)


@router.get("", response_model=list[FavoriteResponse])
def list_favorites(target_type: Literal["local", "shared"] | None = None, db: Session = Depends(get_db)):
    """收藏列表(local 目标附带资料引用)"""
    query = db.query(Favorite)
    if target_type:
        query = query.filter(Favorite.target_type == target_type)
    # 暂离已整理集合时保留引用，但不作为可用的个人收藏展示。
    from sqlalchemy import or_
    query = query.filter(or_(Favorite.target_type != 'local', Favorite.local_document_id.in_(
        db.query(Document.id).filter(Document.status == 'organized'))))
    favs = query.order_by(Favorite.created_at.desc()).all()
    local_ids = {fav.local_document_id for fav in favs if fav.target_type == 'local'}
    docs = db.query(Document).filter(Document.id.in_(local_ids)).all() if local_ids else []
    responses = {doc['id']: doc for doc in build_document_responses(docs, db)}
    return [_to_response(fav, db, responses) for fav in favs]


@router.get('/{favorite_id}/resolve')
def resolve_shared_favorite(favorite_id: str, db: Session = Depends(get_db)):
    from backend.app.api.sharing import _cloud_context, _passthrough, _verified_identity
    from backend.app.core.cloud_client import cloud_req

    fav = db.get(Favorite, favorite_id)
    if not fav or fav.target_type != 'shared':
        raise HTTPException(404, '共享收藏不存在')
    config, server, _ = _cloud_context(fav.workspace_id)
    if not fav.server_id and server.rstrip('/') != fav.server_url.rstrip('/'):
        raise HTTPException(409, '请先连接收藏所属的共享服务器')
    # 仅访问当前配置的服务器，收藏的 URL 不能决定令牌发送目标。
    path = f'/api/workspaces/{quote(fav.workspace_id, safe="")}/documents/{quote(fav.shared_document_id, safe="")}'
    try:
        server_id, _ = _verified_identity(config, expected_server_id=fav.server_id)
        response = cloud_req('GET', server + path, config, timeout=15)
        if response.status_code != 200:
            _passthrough(response)
        fav.server_id, fav.server_url = server_id, server.rstrip('/')
        db.commit()
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, '共享服务不可达，请重试') from exc


@router.delete("/{favorite_id}")
def remove_favorite(favorite_id: str, db: Session = Depends(get_db)):
    """取消收藏"""
    fav = db.get(Favorite, favorite_id)
    if not fav:
        raise HTTPException(404, "收藏不存在")
    db.delete(fav)
    db.commit()
    return {"message": "已取消收藏"}
