"""
设备注册 API
POST /api/devices/register   设备注册/心跳
GET  /api/devices            列出当前用户的设备
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import User, Device
from app.core.deps import get_current_user
from app.schemas.document import DeviceRegisterRequest, DeviceResponse

router = APIRouter(prefix="/api/devices", tags=["设备"])


@router.post("/register", response_model=DeviceResponse)
async def register_device(
    body: DeviceRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    设备注册或心跳更新
    如果设备已存在则更新 last_seen_at 和设备名称
    """
    device = db.query(Device).filter(
        Device.user_id == current_user.id,
        Device.device_id == body.device_id,
    ).first()

    if device:
        device.last_seen_at = datetime.now(timezone.utc)
        if body.device_name:
            device.device_name = body.device_name
    else:
        device = Device(
            user_id=current_user.id,
            device_id=body.device_id,
            device_name=body.device_name,
            platform=body.platform,
            sync_cursors={},
        )
        db.add(device)

    db.commit()
    db.refresh(device)
    return device


@router.get("", response_model=List[DeviceResponse])
async def list_devices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户的所有注册设备"""
    return db.query(Device).filter(Device.user_id == current_user.id).all()


@router.put("/{device_id}/cursor", status_code=200)
async def update_sync_cursor(
    device_id: str,
    workspace_id: str,
    sequence: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新设备在指定工作空间的同步游标"""
    device = db.query(Device).filter(
        Device.user_id == current_user.id,
        Device.device_id == device_id,
    ).first()

    if not device:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="设备不存在")

    cursors = device.sync_cursors or {}
    cursors[workspace_id] = sequence
    device.sync_cursors = cursors
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "游标更新成功"}
