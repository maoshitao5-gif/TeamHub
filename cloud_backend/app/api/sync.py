"""
增量同步 API
POST /api/workspaces/{ws_id}/sync/push   推送本地变更，返回冲突列表
GET  /api/workspaces/{ws_id}/sync/pull   拉取变更（?since_sequence=游标）
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import User, CloudDocument, CloudVersion, CloudTag, CloudChangeLog
from app.core.deps import get_current_user, check_workspace_access, next_sequence_number
from app.schemas.document import (
    SyncPushRequest, SyncPushResponse, ConflictItem, SyncPullResponse,
)

router = APIRouter(tags=["同步"])


def _append_log(ws_id: str, entity_type: str, entity_id: str, operation: str,
                payload: dict, device_id: str | None, db: Session):
    """写入一条变更日志"""
    seq = next_sequence_number(ws_id, db)
    db.add(CloudChangeLog(
        workspace_id=ws_id,
        sequence_number=seq,
        entity_type=entity_type,
        entity_id=entity_id,
        operation=operation,
        payload=payload,
        device_id=device_id,
    ))


def _handle_doc_change(ws_id: str, change, device_id: str | None, db: Session) -> bool:
    """
    处理文档变更，返回 True 表示已接受，False 表示冲突
    """
    operation = change.operation
    payload = change.payload

    if operation == "create":
        if not db.query(CloudDocument).filter(CloudDocument.id == change.entity_id).first():
            doc = CloudDocument(
                id=change.entity_id,
                workspace_id=ws_id,
                name=payload.get("name", "未命名"),
                description=payload.get("description"),
                is_folder=payload.get("is_folder", False),
                status=payload.get("status", "pending"),
                file_count=payload.get("file_count", 1),
                total_size=payload.get("total_size", 0),
            )
            db.add(doc)
            _append_log(ws_id, "document", change.entity_id, "create", payload, device_id, db)
        return True

    doc = db.query(CloudDocument).filter(
        CloudDocument.id == change.entity_id, CloudDocument.workspace_id == ws_id
    ).first()
    if not doc:
        return True  # 文档不存在，视为已处理

    # 冲突检测：客户端版本落后于服务端版本
    if change.client_version is not None and change.client_version < doc.server_version:
        return False  # 返回 False 表示冲突

    if operation == "update":
        for field in ["name", "description", "status", "file_count", "total_size"]:
            if field in payload:
                setattr(doc, field, payload[field])
        doc.server_version += 1
        _append_log(ws_id, "document", doc.id, "update", payload, device_id, db)

    elif operation == "trash":
        doc.status = "trashed"
        doc.trashed_at = datetime.now(timezone.utc)
        doc.server_version += 1
        _append_log(ws_id, "document", doc.id, "trash", {}, device_id, db)

    elif operation == "restore":
        doc.status = "organized"
        doc.trashed_at = None
        doc.server_version += 1
        _append_log(ws_id, "document", doc.id, "restore", {}, device_id, db)

    elif operation == "delete":
        _append_log(ws_id, "document", doc.id, "delete", {}, device_id, db)
        db.delete(doc)

    return True


def _handle_version_change(ws_id: str, change, device_id: str | None, db: Session):
    """处理版本变更"""
    payload = change.payload
    if change.operation != "create":
        return
    doc_id = payload.get("document_id")
    if not doc_id:
        return
    doc = db.query(CloudDocument).filter(
        CloudDocument.id == doc_id, CloudDocument.workspace_id == ws_id
    ).first()
    if not doc:
        return
    if not db.query(CloudVersion).filter(CloudVersion.id == change.entity_id).first():
        ver = CloudVersion(
            id=change.entity_id,
            document_id=doc_id,
            version_number=payload.get("version_number", 1),
            sha256_hash=payload.get("sha256_hash"),
            oss_key=payload.get("oss_key"),
            file_size=payload.get("file_size", 0),
            original_filename=payload.get("original_filename", ""),
            relative_path=payload.get("relative_path"),
            note=payload.get("note"),
            is_current=payload.get("is_current", True),
        )
        db.add(ver)
        _append_log(ws_id, "version", change.entity_id, "create", payload, device_id, db)


def _handle_tag_change(ws_id: str, change, device_id: str | None, db: Session):
    """处理标签变更"""
    payload = change.payload
    if change.operation == "create":
        if not db.query(CloudTag).filter(CloudTag.id == change.entity_id).first():
            tag = CloudTag(
                id=change.entity_id,
                workspace_id=ws_id,
                name=payload.get("name", ""),
                color=payload.get("color"),
            )
            db.add(tag)
            _append_log(ws_id, "tag", change.entity_id, "create", payload, device_id, db)
    elif change.operation == "delete":
        tag = db.query(CloudTag).filter(
            CloudTag.id == change.entity_id, CloudTag.workspace_id == ws_id
        ).first()
        if tag:
            _append_log(ws_id, "tag", change.entity_id, "delete", {}, device_id, db)
            db.delete(tag)


@router.post("/api/workspaces/{ws_id}/sync/push", response_model=SyncPushResponse)
async def sync_push(
    ws_id: str,
    body: SyncPushRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    推送本地变更到云端
    支持冲突检测：当客户端 client_version < 云端 server_version 时判定为冲突
    """
    check_workspace_access(ws_id, current_user.id, "member", db)

    conflicts: List[ConflictItem] = []

    # 第一轮：处理文档变更（确保文档先于版本在 DB 中存在）
    for change in body.changes:
        if change.entity_type == "document":
            accepted = _handle_doc_change(ws_id, change, body.device_id, db)
            if not accepted:
                doc = db.query(CloudDocument).filter(CloudDocument.id == change.entity_id).first()
                conflicts.append(ConflictItem(
                    entity_id=change.entity_id,
                    entity_type="document",
                    client_version=change.client_version,
                    server_version=doc.server_version if doc else 0,
                ))

    # 显式 flush：让第二轮查询能找到本轮新增的文档（同一事务内可见）
    db.flush()

    # 第二轮：处理版本变更（依赖文档已存在）
    for change in body.changes:
        if change.entity_type == "version":
            _handle_version_change(ws_id, change, body.device_id, db)

    # 第三轮：处理标签变更
    for change in body.changes:
        if change.entity_type == "tag":
            _handle_tag_change(ws_id, change, body.device_id, db)

    db.commit()

    accepted_count = len(body.changes) - len(conflicts)
    return SyncPushResponse(accepted=accepted_count, conflicts=conflicts)


@router.get("/api/workspaces/{ws_id}/sync/pull", response_model=SyncPullResponse)
async def sync_pull(
    ws_id: str,
    since_sequence: int = Query(0, description="上次同步的游标，拉取此序号之后的变更"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    增量拉取变更（cursor-based）
    返回 since_sequence 之后的所有变更
    """
    check_workspace_access(ws_id, current_user.id, "viewer", db)

    logs = db.query(CloudChangeLog).filter(
        CloudChangeLog.workspace_id == ws_id,
        CloudChangeLog.sequence_number > since_sequence,
    ).order_by(CloudChangeLog.sequence_number.asc()).all()

    changes = [
        {
            "sequence_number": log.sequence_number,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "operation": log.operation,
            "payload": log.payload,
            "device_id": log.device_id,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]

    latest_seq = logs[-1].sequence_number if logs else since_sequence
    return SyncPullResponse(changes=changes, latest_sequence=latest_seq)
