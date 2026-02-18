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
from backend.app.api.versions import router as versions_router
from backend.app.api.tags import router as tags_router
from backend.app.api.settings_api import router as settings_router

logger = get_logger("main")


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

                    # 恢复其他设置
                    if 'default_storage_mode' in saved_config:
                        settings.default_storage_mode = saved_config['default_storage_mode']
                    if 'max_versions' in saved_config:
                        settings.max_versions = saved_config['max_versions']
                    if 'max_version_age_days' in saved_config:
                        settings.max_version_age_days = saved_config['max_version_age_days']
                    if 'trash_auto_clean_days' in saved_config:
                        settings.trash_auto_clean_days = saved_config['trash_auto_clean_days']
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
app.include_router(versions_router)
app.include_router(tags_router)
app.include_router(settings_router)


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
