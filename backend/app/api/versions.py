"""
版本 API 路由
包含版本的创建、查看、恢复、删除等操作
"""
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Document, Version
from backend.app.config import settings
from backend.app.core.file_utils import calculate_sha256
from backend.app.core.logger import get_logger
from backend.app.api.schemas import VersionCreate, VersionResponse

logger = get_logger("api.versions")
router = APIRouter(prefix="/api/documents/{document_id}/versions", tags=["versions"])


def _compute_hash_background(version_id: str, file_path: str):
    """后台计算文件哈希"""
    from backend.app.database import SessionLocal
    try:
        if os.path.isfile(file_path):
            sha256 = calculate_sha256(file_path)
            db = SessionLocal()
            try:
                version = db.query(Version).filter(Version.id == version_id).first()
                if version:
                    version.sha256_hash = sha256
                    db.commit()
            finally:
                db.close()
    except Exception as e:
        logger.error(f"Background hash calculation failed: {e}")


@router.get("", response_model=list[VersionResponse])
def list_versions(document_id: str, db: Session = Depends(get_db)):
    """获取文档的所有版本"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    versions = db.query(Version).filter(
        Version.document_id == document_id
    ).order_by(Version.version_number.desc()).all()

    return versions


@router.post("", response_model=VersionResponse)
def add_version(
    document_id: str,
    req: VersionCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """为文档添加新版本"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    source = Path(req.file_path)
    if not source.exists():
        raise HTTPException(400, f"文件不存在: {req.file_path}")

    # 获取当前最大版本号
    max_version = db.query(Version).filter(
        Version.document_id == document_id
    )
    if req.relative_path:
        max_version = max_version.filter(Version.relative_path == req.relative_path)
    else:
        max_version = max_version.filter(Version.relative_path.is_(None))

    max_version = max_version.order_by(Version.version_number.desc()).first()
    new_version_number = (max_version.version_number + 1) if max_version else 1

    # 将旧的当前版本标记为非当前
    if max_version and max_version.is_current:
        max_version.is_current = False

    # 处理文件：对于单文件文档，将旧版本移入 .versions/ 目录
    if not doc.is_folder and not req.relative_path:
        if doc.storage_path and settings.library_path:
            library = Path(settings.library_path)
            current_file = library / doc.storage_path

            if current_file.exists() and max_version:
                # 移动旧版本到 .versions/
                versions_dir = current_file.parent / '.versions'
                versions_dir.mkdir(exist_ok=True)
                old_name = f"{current_file.stem}_v{max_version.version_number}{current_file.suffix}"
                old_path = versions_dir / old_name
                shutil.copy2(str(current_file), str(old_path))
                max_version.file_path = str(old_path)

            # 新版本替换当前文件
            if source != current_file:
                shutil.copy2(str(source), str(current_file))
            new_file_path = str(current_file)
        else:
            new_file_path = str(source)
    else:
        new_file_path = str(source)

    file_size = os.path.getsize(new_file_path) if os.path.isfile(new_file_path) else 0

    version = Version(
        document_id=document_id,
        version_number=new_version_number,
        file_path=new_file_path,
        relative_path=req.relative_path,
        file_size=file_size,
        original_filename=source.name,
        note=req.note,
        is_current=True
    )
    db.add(version)
    db.commit()
    db.refresh(version)

    # 后台计算哈希
    background_tasks.add_task(_compute_hash_background, version.id, new_file_path)

    return version


@router.post("/{version_id}/restore")
def restore_version(document_id: str, version_id: str, db: Session = Depends(get_db)):
    """恢复某个版本为当前版本"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    target_version = db.query(Version).filter(
        Version.id == version_id,
        Version.document_id == document_id
    ).first()
    if not target_version:
        raise HTTPException(404, "版本不存在")

    if target_version.is_current:
        return {"message": "该版本已经是当前版本"}

    # 获取同一相对路径下的所有版本
    query = db.query(Version).filter(Version.document_id == document_id)
    if target_version.relative_path:
        query = query.filter(Version.relative_path == target_version.relative_path)
    else:
        query = query.filter(Version.relative_path.is_(None))

    # 将所有版本标记为非当前
    for v in query.all():
        v.is_current = False

    # 恢复目标版本
    target_version.is_current = True

    # 对于单文件文档，将恢复版本的文件复制回当前位置
    if not doc.is_folder and not target_version.relative_path:
        if doc.storage_path and settings.library_path:
            library = Path(settings.library_path)
            current_path = library / doc.storage_path
            old_path = Path(target_version.file_path)
            if old_path.exists() and old_path != current_path:
                shutil.copy2(str(old_path), str(current_path))
                target_version.file_path = str(current_path)

    db.commit()
    return {"message": f"已恢复到版本 v{target_version.version_number}"}


@router.delete("/{version_id}")
def delete_version(document_id: str, version_id: str, db: Session = Depends(get_db)):
    """删除某个版本"""
    version = db.query(Version).filter(
        Version.id == version_id,
        Version.document_id == document_id
    ).first()
    if not version:
        raise HTTPException(404, "版本不存在")

    if version.is_current:
        raise HTTPException(400, "不能删除当前版本")

    # 删除物理文件（如果在 .versions/ 目录中）
    file_path = Path(version.file_path)
    if file_path.exists() and '.versions' in str(file_path):
        file_path.unlink()

    db.delete(version)
    db.commit()
    return {"message": "版本已删除"}
