"""
云后端 — 共享收件箱 API
层：云后端（FastAPI，端口 9000，部署阿里云）

所有端点均需 JWT 认证。
前端（Vue 3）通过 cloudOss.js 调用本模块，流程：
  1. 本地后端 quick_share
       → POST /api/sharing/presign-upload 获取预签名上传 URL（云后端持有 OSS 凭证）
       → 本地后端 PUT 文件到预签名 URL（直传 OSS，云后端不传输文件内容）
       → POST /api/sharing/publish 更新 manifest（写入共享清单）
  2. 云后端 /inbox → 列出所有设备共享文档（读 manifest）
  3. 云后端 /presign-download → 返回预签名下载 URL（前端 + Electron IPC 下载文件到本机）
  4. 云后端 /withdraw → 撤回共享（删除 OSS 文件 + 更新 manifest）
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user
from app.core import oss as oss_ops
from app.models import User, Device

router = APIRouter(prefix="/api/sharing", tags=["sharing"])


# ===========================================================================
# 端点：获取共享文件预签名上传 URL（本地后端调用，云后端持有 OSS 凭证）
# ===========================================================================

class SharedPresignUploadRequest(BaseModel):
    device_id: str
    doc_id: str
    filename: str
    content_type: str = "application/octet-stream"


@router.post("/presign-upload")
def presign_upload_shared(
    body: SharedPresignUploadRequest,
    current_user: User = Depends(get_current_user),
):
    """
    为共享文件生成预签名上传 URL（有效期 15 分钟）。
    本地后端拿到 URL 后直接 PUT 文件到 OSS，云后端不传输文件内容。
    """
    oss_key = oss_ops._shared_file_key(body.device_id, body.doc_id, body.filename)
    try:
        upload_url = oss_ops.generate_presign_upload_url(oss_key, body.content_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成上传链接失败: {e}")
    return {"upload_url": upload_url, "oss_key": oss_key}


# ===========================================================================
# 端点：发布共享文档（上传完成后更新 manifest）
# ===========================================================================

class PublishRequest(BaseModel):
    device_id: str
    device_name: str
    doc_id: str
    name: str
    filename: str
    file_size: int
    oss_key: str
    visibility: str = "public"
    tags: List[str] = []
    is_folder: bool = False
    file_count: Optional[int] = None
    updated_at: Optional[str] = None
    sha256_hash: Optional[str] = None  # 文件 SHA-256，供接收方查重


@router.post("/publish")
def publish_shared(
    body: PublishRequest,
    current_user: User = Depends(get_current_user),
):
    """
    文件上传完成后调用：将文档信息写入设备的 manifest，使其出现在云仓库列表中。
    oss_key 必须匹配 shared/{device_id}/ 前缀（防止越权写入其他设备的共享目录）。
    """
    # 验证 oss_key 归属
    expected_key = oss_ops._shared_file_key(body.device_id, body.doc_id, body.filename)
    if body.oss_key != expected_key:
        raise HTTPException(status_code=400, detail="oss_key 与设备/文档/文件名不匹配")

    # 读取或初始化 manifest
    manifest = oss_ops.get_shared_manifest(body.device_id) or {
        "device_id": body.device_id,
        "device_name": body.device_name,
        "docs": [],
    }
    manifest["device_name"] = body.device_name
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

    entry = {
        "doc_id": body.doc_id,
        "name": body.name,
        "filename": body.filename,
        "file_size": body.file_size,
        "is_folder": body.is_folder,
        "file_count": body.file_count,
        "updated_at": body.updated_at or datetime.now(timezone.utc).isoformat(),
        "tags": body.tags,
        "visibility": body.visibility,
        "oss_key": body.oss_key,
        "sha256_hash": body.sha256_hash,  # 供接收方查重使用
    }

    manifest["docs"] = [d for d in manifest.get("docs", []) if d.get("doc_id") != body.doc_id]
    manifest["docs"].append(entry)

    try:
        oss_ops.put_shared_manifest(body.device_id, manifest)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"写入 manifest 失败: {e}")

    return {"ok": True, "doc_id": body.doc_id}


# ===========================================================================
# 端点：OSS 配置状态
# ===========================================================================

@router.get("/status")
def get_status(current_user: User = Depends(get_current_user)):
    """查询云端存储状态（是否已配置 OSS）"""
    from app.config import settings
    configured = bool(
        settings.oss_endpoint
        and settings.oss_access_key_id
        and settings.oss_access_key_secret
        and settings.oss_bucket_name
    )
    return {
        "configured": configured,
        "endpoint": settings.oss_endpoint if configured else None,
        "bucket": settings.oss_bucket_name if configured else None,
    }


# ===========================================================================
# 端点：共享收件箱列表
# ===========================================================================

@router.get("/inbox")
def get_inbox(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """列出云仓库文档：
    - 本用户名下的设备：公开和私有均显示（私有文档对自己可见，用于备份）
    - 其他用户的设备：仅显示公开文档
    """
    # 获取当前用户拥有的所有设备 ID（用于判断文档归属）
    my_device_ids = {
        d.device_id
        for d in db.query(Device).filter(Device.user_id == current_user.id).all()
    }

    device_ids = oss_ops.list_shared_device_ids()
    all_docs = []
    for device_id in device_ids:
        manifest = oss_ops.get_shared_manifest(device_id)
        if not manifest:
            continue
        device_name = manifest.get("device_name", device_id)
        is_my_device = device_id in my_device_ids
        for doc in manifest.get("docs", []):
            # 私有文档只有设备所有者可见
            if doc.get("visibility", "public") != "public" and not is_my_device:
                continue
            all_docs.append({
                **doc,
                "device_id": device_id,
                "device_name": device_name,
            })
    return {"docs": all_docs}


# ===========================================================================
# 端点：预签名下载 URL
# ===========================================================================

@router.get("/presign-download/{device_id}/{doc_id}/{filename}")
def presign_download(
    device_id: str,
    doc_id: str,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    返回 OSS 预签名下载 URL（有效期 1 小时）。
    私有文档只有所属设备的用户可以下载。
    """
    # 读取 manifest，检查文档可见性
    manifest = oss_ops.get_shared_manifest(device_id)
    if manifest:
        doc_entry = next(
            (d for d in manifest.get("docs", []) if d.get("doc_id") == doc_id),
            None,
        )
        if doc_entry and doc_entry.get("visibility", "public") == "private":
            # 私有文档：仅设备所有者可下载
            owned = db.query(Device).filter(
                Device.user_id == current_user.id,
                Device.device_id == device_id,
            ).first()
            if not owned:
                raise HTTPException(status_code=403, detail="无权下载该私有文档")

    oss_key = oss_ops._shared_file_key(device_id, doc_id, filename)
    try:
        url = oss_ops.generate_presign_download_url(oss_key, expires=3600)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成预签名 URL 失败: {e}")
    return {"url": url, "expires_in": 3600}


# ===========================================================================
# 端点：撤回共享
# ===========================================================================

@router.delete("/{device_id}/{doc_id}")
def withdraw_shared(
    device_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤回共享：删除 OSS shared/{device_id}/{doc_id}/ 下所有文件，并更新 manifest"""
    # 校验设备归属：只有设备所有者才可撤回
    owned = db.query(Device).filter(
        Device.user_id == current_user.id,
        Device.device_id == device_id,
    ).first()
    if not owned:
        raise HTTPException(status_code=403, detail="无权操作该设备的共享文件")

    deleted = oss_ops.delete_shared_doc(device_id, doc_id)

    manifest = oss_ops.get_shared_manifest(device_id)
    if manifest:
        manifest["docs"] = [d for d in manifest["docs"] if d.get("doc_id") != doc_id]
        oss_ops.put_shared_manifest(device_id, manifest)

    return {"ok": True, "deleted_objects": deleted}
