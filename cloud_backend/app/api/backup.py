"""
云后端 — 本地数据库备份 API
层：云后端（FastAPI，端口 9000）

流程：
  1. 本地后端打包 db.sqlite + config.json 为 zip
  2. POST /api/backup/presign-upload → 创建备份记录，返回预签名上传 URL
  3. 本地后端直接 PUT zip 到 OSS（云后端不传输文件内容）
  4. POST /api/backup/{backup_id}/confirm → 确认上传成功
  5. 换电脑后：GET /api/backup/list → 选择备份
  6. GET /api/backup/{backup_id}/presign-download → 下载 URL
  7. DELETE /api/backup/{backup_id} → 删除备份
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user
from app.core import oss as oss_ops
from app.config import settings
from app.models import User, UserBackup

router = APIRouter(tags=["backup"])


def _backup_oss_key(user_id: str, backup_id: str, filename: str) -> str:
    """备份文件的 OSS key 格式：{prefix}/backups/{user_id}/{backup_id}/{filename}"""
    return f"{settings.oss_prefix}/backups/{user_id}/{backup_id}/{filename}"


# ===========================================================================
# 端点：获取预签名上传 URL
# ===========================================================================

class BackupPresignUploadRequest(BaseModel):
    filename: str           # teamhub-backup-YYYYMMDD-HHMMSS.zip
    file_size: int
    device_id: str
    device_name: str = ""


@router.post("/presign-upload")
def presign_upload_backup(
    body: BackupPresignUploadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    为备份文件生成预签名上传 URL（有效期 15 分钟）。
    同时创建 UserBackup 记录（confirmed=False），等待上传确认。
    """
    from app.models import _gen_uuid
    backup_id = _gen_uuid()
    oss_key = _backup_oss_key(current_user.id, backup_id, body.filename)

    try:
        upload_url = oss_ops.generate_presign_upload_url(
            oss_key,
            content_type="application/zip",
            expires=900,   # 15 分钟
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成上传链接失败: {e}")

    backup = UserBackup(
        id=backup_id,
        user_id=current_user.id,
        device_id=body.device_id,
        device_name=body.device_name,
        filename=body.filename,
        file_size=body.file_size,
        oss_key=oss_key,
        confirmed=False,
    )
    db.add(backup)
    db.commit()

    return {
        "backup_id": backup_id,
        "upload_url": upload_url,
        "oss_key": oss_key,
    }


# ===========================================================================
# 端点：确认上传完成
# ===========================================================================

@router.post("/{backup_id}/confirm")
def confirm_backup_upload(
    backup_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """本地后端 PUT 文件到 OSS 成功后调用，将备份记录标记为有效。"""
    backup = db.query(UserBackup).filter(
        UserBackup.id == backup_id,
        UserBackup.user_id == current_user.id,
    ).first()
    if not backup:
        raise HTTPException(status_code=404, detail="备份记录不存在")

    backup.confirmed = True
    db.commit()
    return {"ok": True, "backup_id": backup_id}


# ===========================================================================
# 端点：备份列表
# ===========================================================================

@router.get("/list")
def list_backups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户所有已确认的备份（按创建时间倒序）。"""
    backups = (
        db.query(UserBackup)
        .filter(
            UserBackup.user_id == current_user.id,
            UserBackup.confirmed == True,
        )
        .order_by(UserBackup.created_at.desc())
        .all()
    )
    return {
        "backups": [
            {
                "id": b.id,
                "device_id": b.device_id,
                "device_name": b.device_name,
                "filename": b.filename,
                "file_size": b.file_size,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in backups
        ]
    }


# ===========================================================================
# 端点：获取预签名下载 URL
# ===========================================================================

@router.get("/{backup_id}/presign-download")
def presign_download_backup(
    backup_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成备份文件的预签名下载 URL（有效期 1 小时）。仅本人可下载。"""
    backup = db.query(UserBackup).filter(
        UserBackup.id == backup_id,
        UserBackup.user_id == current_user.id,
        UserBackup.confirmed == True,
    ).first()
    if not backup:
        raise HTTPException(status_code=404, detail="备份记录不存在或尚未确认")

    try:
        download_url = oss_ops.generate_presign_download_url(backup.oss_key, expires=3600)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成下载链接失败: {e}")

    return {"download_url": download_url, "expires_in": 3600, "filename": backup.filename}


# ===========================================================================
# 端点：删除备份
# ===========================================================================

@router.delete("/{backup_id}")
def delete_backup(
    backup_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除 OSS 文件及数据库记录。仅本人可操作。"""
    backup = db.query(UserBackup).filter(
        UserBackup.id == backup_id,
        UserBackup.user_id == current_user.id,
    ).first()
    if not backup:
        raise HTTPException(status_code=404, detail="备份记录不存在")

    # 删除 OSS 文件（忽略删除失败，确保 DB 记录始终清除）
    try:
        oss_ops.delete_object(backup.oss_key)
    except Exception:
        pass

    db.delete(backup)
    db.commit()
    return {"ok": True, "backup_id": backup_id}
