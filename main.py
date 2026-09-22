"""
TeamHub 本地版 — FastAPI 应用入口
"""
import os
import sys
import json
from contextlib import asynccontextmanager
from pathlib import Path

# 全局 UTF-8 编码配置
from backend.app.core.encoding import setup_utf8_encoding
setup_utf8_encoding()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.config import settings
from backend.app.database import init_db, load_library_config
from backend.app.core.logger import get_logger
from backend.app.middleware.encoding import UTF8EncodingMiddleware
from backend.app.middleware.exception import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler
)

# API 路由
from backend.app.api.documents import router as documents_router
from backend.app.api.tags import router as tags_router
from backend.app.api.settings_api import router as settings_router
from backend.app.api.sync import router as sync_router
from backend.app.api.share import router as share_router

logger = get_logger("main")


def _apply_config(s, cfg: dict):
    """将配置字典中的已知字段应用到 settings 对象"""
    field_map = [
        'default_storage_mode', 'max_versions', 'max_version_age_days',
        'trash_auto_clean_days', 'max_file_size', 'on_conflict',
        'default_sort_by', 'default_sort_order', 'auto_scan_on_startup',
        'enable_floating_window', 'items_per_page',
        'library_show_flat_view', 'library_show_tree_view',
        'pending_folder_name', 'cloud_api_url',
    ]
    for key in field_map:
        if key in cfg:
            setattr(s, key, cfg[key])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("TeamHub 启动中...")

    # 尝试从配置文件恢复 library_path
    try:
        # 先检查 Electron 环境的配置目录
        if settings.is_electron:
            config_path = Path(settings.user_data_dir) / 'config' / 'config.json'
        else:
            config_path = Path('config.json')

        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
            if 'library_path' in saved_config and saved_config['library_path']:
                lib_path = saved_config['library_path']
                if Path(lib_path).exists():
                    settings.library_path = lib_path
                    logger.info(f"Restored library path: {lib_path}")

                    # 优先从库目录的 config.json 读取完整设置
                    library_config_path = Path(lib_path) / '.teamhub' / 'config.json'
                    if library_config_path.exists():
                        try:
                            with open(library_config_path, 'r', encoding='utf-8') as lf:
                                lib_cfg = json.load(lf)
                            _apply_config(settings, lib_cfg)
                            logger.info("Loaded settings from library config")
                        except Exception as le:
                            logger.warning(f"Failed to load library config, falling back: {le}")
                            _apply_config(settings, saved_config)
                    else:
                        # 兼容旧版：从 bootstrap config 读取
                        _apply_config(settings, saved_config)
    except Exception as e:
        logger.warning(f"Failed to load saved config: {e}")

    # 初始化数据库
    init_db()

    # 回收站自动清理
    if settings.library_path and settings.trash_auto_clean_days > 0:
        try:
            from backend.app.core.trash_cleaner import cleanup_old_trash
            cleaned = cleanup_old_trash(settings.trash_auto_clean_days, settings.library_path)
            if cleaned > 0:
                logger.info(f"启动时自动清理了 {cleaned} 个过期回收站文档")
        except Exception as e:
            logger.warning(f"回收站自动清理失败: {e}")

    # 启动时自动扫描文件库
    if settings.library_path and settings.auto_scan_on_startup:
        try:
            from backend.app.database import SessionLocal
            from backend.app.models import Document
            import os as _os
            db = SessionLocal()
            try:
                from pathlib import Path as _Path
                library = _Path(settings.library_path)
                docs = db.query(Document).filter(Document.status.in_(["organized", "pending"])).all()
                missing_count = 0
                for doc in docs:
                    if doc.storage_mode == "index":
                        check_path = doc.original_path
                    elif doc.storage_path:
                        check_path = str(library / doc.storage_path)
                    else:
                        continue
                    if check_path and not _os.path.exists(check_path):
                        doc.status = "missing"
                        missing_count += 1
                db.commit()
                logger.info(f"启动时自动扫描完成：共 {len(docs)} 个文档，{missing_count} 个缺失")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"启动时自动扫描失败: {e}")

    logger.info("TeamHub 启动完成")
    if settings.library_path:
        logger.info(f"文件库: {settings.library_path}")
    else:
        logger.info("文件库未设置，等待用户初始化")

    yield

    logger.info("TeamHub 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="TeamHub 本地版",
    description="个人文件整理与版本管理工具",
    version="2.0.0",
    lifespan=lifespan
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# UTF-8 编码中间件
app.add_middleware(UTF8EncodingMiddleware)

# 异常处理器
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# 注册路由
app.include_router(documents_router)
app.include_router(tags_router)
app.include_router(settings_router)
app.include_router(sync_router)
app.include_router(share_router)


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "library_initialized": settings.library_path is not None,
        "library_path": settings.library_path,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
