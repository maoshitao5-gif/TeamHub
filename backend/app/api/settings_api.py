"""
设置 API 路由
包含文件库初始化、配置管理等
"""
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.database import get_db, init_db, save_library_config, load_library_config
from backend.app.models import Document
from backend.app.config import settings
from backend.app.core.logger import get_logger
from backend.app.api.schemas import LibrarySetup, SettingsUpdate, LibraryInfoResponse

logger = get_logger("api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/library", response_model=LibraryInfoResponse)
def get_library_info(db: Session = Depends(get_db)):
    """获取文件库信息"""
    initialized = settings.library_path is not None and Path(settings.library_path).exists()

    total_docs = 0
    total_size = 0
    pending_count = 0

    if initialized:
        total_docs = db.query(Document).filter(Document.status.in_(["organized", "pending"])).count()
        total_size = db.query(func.coalesce(func.sum(Document.total_size), 0)).filter(
            Document.status.in_(["organized", "pending"])
        ).scalar()
        pending_count = db.query(Document).filter(Document.status == "pending").count()

    return LibraryInfoResponse(
        path=settings.library_path,
        initialized=initialized,
        total_documents=total_docs,
        total_size=total_size,
        pending_count=pending_count
    )


@router.post("/library")
def setup_library(req: LibrarySetup):
    """初始化文件库（首次设置或更换路径）"""
    path = Path(req.path)

    # 创建文件库目录
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"无法创建目录: {e}")

    # 检查目录可写
    test_file = path / '.teamhub_test'
    try:
        test_file.write_text('test')
        test_file.unlink()
    except OSError as e:
        raise HTTPException(400, f"目录不可写: {e}")

    # 更新设置
    settings.library_path = str(path.resolve())

    # 初始化目录结构和数据库
    init_db()

    # 保存配置
    config = load_library_config()
    config['library_path'] = settings.library_path
    config['default_storage_mode'] = settings.default_storage_mode
    config['max_versions'] = settings.max_versions
    config['max_version_age_days'] = settings.max_version_age_days
    config['trash_auto_clean_days'] = settings.trash_auto_clean_days
    save_library_config(config)

    logger.info(f"Library initialized at: {settings.library_path}")
    return {"message": "文件库初始化成功", "path": settings.library_path}


@router.get("/config")
def get_settings():
    """获取当前设置"""
    return {
        "library_path": settings.library_path,
        "default_storage_mode": settings.default_storage_mode,
        "max_versions": settings.max_versions,
        "max_version_age_days": settings.max_version_age_days,
        "trash_auto_clean_days": settings.trash_auto_clean_days,
        "max_file_size": settings.max_file_size,
    }


@router.put("/config")
def update_settings(req: SettingsUpdate):
    """更新设置"""
    if req.default_storage_mode is not None:
        settings.default_storage_mode = req.default_storage_mode
    if req.max_versions is not None:
        settings.max_versions = req.max_versions
    if req.max_version_age_days is not None:
        settings.max_version_age_days = req.max_version_age_days
    if req.trash_auto_clean_days is not None:
        settings.trash_auto_clean_days = req.trash_auto_clean_days

    # 持久化
    config = load_library_config()
    config.update({k: v for k, v in req.model_dump().items() if v is not None})
    save_library_config(config)

    return {"message": "设置已更新"}


@router.post("/scan")
def scan_library(db: Session = Depends(get_db)):
    """
    启动时扫描：检查所有文档的物理路径是否存在
    将不存在的标记为 missing
    """
    if not settings.library_path:
        return {"message": "文件库未初始化", "scanned": 0, "missing": 0}

    library = Path(settings.library_path)
    docs = db.query(Document).filter(Document.status.in_(["organized", "pending"])).all()

    missing_count = 0
    for doc in docs:
        if doc.storage_mode == "index":
            # 索引模式检查原始路径
            check_path = doc.original_path
        elif doc.storage_path:
            check_path = str(library / doc.storage_path)
        else:
            continue

        if check_path and not os.path.exists(check_path):
            doc.status = "missing"
            missing_count += 1

    db.commit()
    return {"message": "扫描完成", "scanned": len(docs), "missing": missing_count}


@router.get("/library/subdirs")
def list_library_subdirs(parent: str = ""):
    """列出文件库中的子目录（用于前端选择存放位置）"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    library = Path(settings.library_path)
    target = library / parent if parent else library

    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "目录不存在")

    dirs = []
    for item in sorted(target.iterdir()):
        if item.is_dir() and not item.name.startswith('.'):
            dirs.append({
                "name": item.name,
                "path": str(item.relative_to(library)),
            })

    return {"dirs": dirs, "current": parent}
