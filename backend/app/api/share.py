"""
本地后端 — 云端共享 API
层：本地后端（FastAPI，端口 8001）

OSS AK/SK 已迁移至云后端管理，本地后端不再持有 OSS 凭证。
文件上传流程：
  1. 调云后端 /api/sharing/presign-upload 获取预签名 URL（JWT 鉴权，云后端持有 OSS 凭证）
  2. 本地后端直接 PUT 文件到预签名 URL（不经过云后端传输文件内容）
  3. 调云后端 /api/sharing/publish 更新 manifest

仅保留两个端点：
  PUT  /api/share/documents/{doc_id}/sync-enabled  — 开关文档云同步标记
  POST /api/share/documents/{doc_id}/quick-share   — 一键共享：读本地文件 → 上传 OSS → 更新 manifest
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db, load_library_config
from backend.app.models import Version, generate_uuid, utc_now
from backend.app.core.device import get_device_id
from backend.app.core.cloud_client import get_cloud_url, cloud_req, _http
from backend.app.core.logger import get_logger
from backend.app.core.file_utils import get_document_or_404
from backend.app.api.schemas import QuickShareRequest
from backend.app.config import settings

logger = get_logger("api.share")
router = APIRouter(prefix="/api/share", tags=["share"])


# ===========================================================================
# 内部工具（委托给 core.cloud_client）
# ===========================================================================

# 短名称别名，保持文件内调用风格一致
_get_cloud_url = get_cloud_url
_cloud_req = cloud_req


# ===========================================================================
# 端点：文档云同步开关
# ===========================================================================

@router.put("/documents/{doc_id}/sync-enabled")
def toggle_doc_sync(doc_id: str, enabled: bool = Query(...), db: Session = Depends(get_db)):
    """开启或关闭文档的云同步标记（不立即推送）"""
    doc = get_document_or_404(db, doc_id)
    doc.cloud_sync_enabled = enabled
    if enabled and doc.sync_status != "synced":
        doc.sync_status = "local"
    db.commit()
    return {"ok": True, "cloud_sync_enabled": doc.cloud_sync_enabled}


# ===========================================================================
# 端点：一键共享
# ===========================================================================

@router.post("/documents/{doc_id}/quick-share")
def quick_share(doc_id: str, req: QuickShareRequest, db: Session = Depends(get_db)):
    """
    一步完成：开启云同步 + 设置可见性 + 通过云后端上传到 OSS shared/ 目录 + 更新 manifest。
    OSS 凭证由云后端持有，本地后端通过预签名 URL 直传，不暴露 AK/SK。
    云服务未登录时返回 400。
    """
    cfg = load_library_config()
    if not cfg.get("cloud_access_token"):
        raise HTTPException(status_code=400, detail="请先在设置中登录云服务")

    cloud_url = _get_cloud_url(cfg)

    doc = get_document_or_404(db, doc_id)

    # 获取当前版本
    current_ver = (
        db.query(Version)
        .filter(Version.document_id == doc_id, Version.is_current == True)
        .first()
    )

    # 非文件夹文档缺少当前版本时，尝试自动修复数据
    if not doc.is_folder and current_ver is None:
        latest_ver = (
            db.query(Version)
            .filter(Version.document_id == doc_id)
            .order_by(Version.version_number.desc())
            .first()
        )
        if latest_ver:
            logger.warning(
                f"quick_share: 文档 {doc_id}({doc.name!r}) 有版本但无当前标记，"
                f"自动修复 v{latest_ver.version_number}"
            )
            latest_ver.is_current = True
            db.flush()
            current_ver = latest_ver
        elif doc.storage_path and settings.library_path:
            file_path = str(Path(settings.library_path) / doc.storage_path)
            if Path(file_path).exists():
                logger.warning(
                    f"quick_share: 文档 {doc_id}({doc.name!r}) 无任何版本，"
                    "自动从磁盘补建版本记录"
                )
                current_ver = Version(
                    id=generate_uuid(),
                    document_id=doc_id,
                    version_number=1,
                    file_path=file_path,
                    file_size=Path(file_path).stat().st_size,
                    original_filename=Path(file_path).name,
                    is_current=True,
                )
                db.add(current_ver)
                db.flush()

    # 确定文件信息
    library_path = settings.library_path or ""
    device_id = get_device_id()
    device_name = cfg.get("device_name", "未命名设备")

    if doc.is_folder:
        fname = doc.name
        file_size = doc.total_size or 0
    elif current_ver is not None:
        fname = current_ver.original_filename or (
            Path(current_ver.file_path).name if current_ver.file_path else ""
        )
        if not fname:
            raise HTTPException(
                status_code=400,
                detail=f"文档「{doc.name}」版本的文件名为空，无法加入共享清单"
            )
        file_size = current_ver.file_size or 0
    else:
        raise HTTPException(status_code=400, detail="文档无有效版本，无法共享")

    # 上传文件到 OSS（仅非文件夹）
    oss_key = None
    if not doc.is_folder and current_ver is not None:
        fpath = current_ver.file_path
        if fpath and not Path(fpath).is_absolute() and library_path:
            fpath = str(Path(library_path) / fpath)
        if not fpath or not Path(fpath).exists():
            raise HTTPException(status_code=400, detail="文档文件不存在，无法共享")

        # 1. 从云后端获取预签名上传 URL
        presign_resp = _cloud_req(
            "POST",
            f"{cloud_url}/api/sharing/presign-upload",
            cfg,
            json={
                "device_id": device_id,
                "doc_id": doc_id,
                "filename": fname,
                "content_type": "application/octet-stream",
            },
        )
        if presign_resp.status_code == 401:
            raise HTTPException(status_code=401, detail="云服务登录已过期，请在设置中重新登录")
        if presign_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"获取上传链接失败: {presign_resp.text}"
            )
        presign_data = presign_resp.json()
        upload_url = presign_data["upload_url"]
        oss_key = presign_data["oss_key"]

        # 2. 直接 PUT 文件到预签名 URL（云后端不传输文件内容）
        try:
            with open(fpath, "rb") as f:
                up_resp = _http.put(
                    upload_url,
                    content=f,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=300,
                )
            if up_resp.status_code not in (200, 204):
                raise HTTPException(
                    status_code=502,
                    detail=f"文件上传 OSS 失败: HTTP {up_resp.status_code}"
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"文件上传失败: {e}")

    # 3. 调云后端发布到 manifest
    # 取当前版本的 sha256（供云仓库查重使用）
    sha256_hash = None
    if current_ver:
        sha256_hash = current_ver.sha256_hash
        if not sha256_hash:
            # 后台可能尚未计算，尝试即时补算（不阻塞上传）
            try:
                from backend.app.core.file_utils import calculate_sha256 as _sha256
                fpath_for_hash = current_ver.file_path
                if fpath_for_hash and not Path(fpath_for_hash).is_absolute() and library_path:
                    fpath_for_hash = str(Path(library_path) / fpath_for_hash)
                if fpath_for_hash and Path(fpath_for_hash).exists():
                    sha256_hash = _sha256(fpath_for_hash)
                    current_ver.sha256_hash = sha256_hash
                    db.flush()
            except Exception:
                pass

    publish_payload = {
        "device_id": device_id,
        "device_name": device_name,
        "doc_id": doc_id,
        "name": doc.name,
        "filename": fname,
        "file_size": file_size,
        "oss_key": oss_key or "",
        "visibility": req.visibility if req.visibility == "private" else "public",
        "tags": [t.name for t in doc.tags],
        "is_folder": doc.is_folder,
        "file_count": doc.file_count if doc.is_folder else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "sha256_hash": sha256_hash,
    }
    publish_resp = _cloud_req(
        "POST",
        f"{cloud_url}/api/sharing/publish",
        cfg,
        json=publish_payload,
    )
    if publish_resp.status_code == 401:
        raise HTTPException(status_code=401, detail="云服务登录已过期，请在设置中重新登录")
    if publish_resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"发布到云端 manifest 失败: {publish_resp.text}"
        )

    # 更新本地文档状态
    doc.cloud_sync_enabled = True
    doc.share_visibility = req.visibility
    doc.sync_status = "synced"
    doc.last_synced_at = utc_now()
    db.commit()

    logger.info(
        f"quick_share 完成: device={device_id} doc={doc_id} "
        f"visibility={publish_payload['visibility']}"
    )
    return {"ok": True, "doc_id": doc_id, "visibility": req.visibility, "in_manifest": True}
