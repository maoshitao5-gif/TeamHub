"""
安全存储 API（服务端持有 OSS 凭证，客户端通过预签名 URL 直传）
POST /api/storage/presign-upload
POST /api/storage/presign-download
POST /api/storage/confirm-upload
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, CloudDocument, CloudVersion
from app.core.deps import get_current_user, check_workspace_access
from app.core.oss import build_oss_key, generate_presign_upload_url, generate_presign_download_url
from app.schemas.document import (
    PresignUploadRequest, PresignUploadResponse,
    PresignDownloadRequest, PresignDownloadResponse,
    ConfirmUploadRequest,
)

router = APIRouter(prefix="/api/storage", tags=["存储"])


@router.post("/presign-upload", response_model=PresignUploadResponse)
async def presign_upload(
    body: PresignUploadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    为文件上传生成预签名 URL
    客户端拿到 URL 后直接 PUT 到 OSS，无需经过云服务传输文件内容
    """
    check_workspace_access(body.workspace_id, current_user.id, "member", db)

    oss_key = build_oss_key(
        workspace_id=body.workspace_id,
        document_id=body.document_id,
        version_id=body.version_id,
        filename=body.filename,
    )

    try:
        upload_url = generate_presign_upload_url(oss_key, body.content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成上传链接失败: {e}")

    return PresignUploadResponse(upload_url=upload_url, oss_key=oss_key)


@router.post("/presign-download", response_model=PresignDownloadResponse)
async def presign_download(
    body: PresignDownloadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """为文件下载生成预签名 URL"""
    # 根据 oss_key 找到对应版本，验证用户对工作空间的访问权限
    version = db.query(CloudVersion).filter(CloudVersion.oss_key == body.oss_key).first()
    if not version:
        raise HTTPException(status_code=404, detail="文件不存在")

    doc = db.query(CloudDocument).filter(CloudDocument.id == version.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    check_workspace_access(doc.workspace_id, current_user.id, "viewer", db)

    try:
        download_url = generate_presign_download_url(body.oss_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成下载链接失败: {e}")

    return PresignDownloadResponse(download_url=download_url)


@router.post("/confirm-upload", status_code=200)
async def confirm_upload(
    body: ConfirmUploadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    客户端上传完成后调用此接口，将 sha256_hash 和 oss_key 写入版本记录
    如果版本记录尚未创建，则新建；如果已存在则更新
    """
    version = db.query(CloudVersion).filter(CloudVersion.id == body.version_id).first()
    if not version:
        # 版本由 sync/push 创建，confirm-upload 可能在其之前调用，忽略即可
        return {"message": "版本记录尚未创建，oss_key 将在推送时同步", "version_id": body.version_id}

    version.sha256_hash = body.sha256_hash
    version.oss_key = body.oss_key
    version.file_size = body.file_size

    db.commit()
    return {"message": "上传确认成功", "version_id": body.version_id}
