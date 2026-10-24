"""
TeamHub 本地版 — FastAPI 应用入口
"""
import os
import sys
import json
from contextlib import asynccontextmanager
from pathlib import Path

os.environ.setdefault('PYDANTIC_AI_NO_BANNER', '1')

# 冻结程序也是受限 PDF 渲染子进程的入口；必须在应用、配置和数据库导入前分流。
if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--teamhub-partner-render':
    from backend.app.services.partner_tools import render_worker_main
    raise SystemExit(render_worker_main())

if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--teamhub-archive-read':
    from backend.app.services.archive_native import main as archive_worker_main
    raise SystemExit(archive_worker_main())

# 全局 UTF-8 编码配置
from backend.app.core.encoding import setup_utf8_encoding
setup_utf8_encoding()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.config import settings, SANDBOX
from backend.app.database import init_db
from backend.app.core.logger import get_logger
from backend.app.middleware.encoding import UTF8EncodingMiddleware
from backend.app.middleware.local_access import LocalAccessMiddleware
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
from backend.app.api.versions import router as versions_router
from backend.app.api.favorites import router as favorites_router
from backend.app.api.sharing import router as sharing_router
from backend.app.api.deliveries import router as deliveries_router
from backend.app.api.ai import router as ai_router
from backend.app.api.indexing import router as indexing_router
from backend.app.api.preorganize import router as preorganize_router
from backend.app.api.document_cards import router as document_cards_router
from backend.app.api.partner import router as partner_router

logger = get_logger("main")


def _apply_config(s, cfg: dict):
    """只应用仍支持的设置；旧版自动版本清理字段保留在文件中但不再启用。"""
    field_map = [
        'default_storage_mode',
        'trash_auto_clean_days', 'max_file_size', 'on_conflict',
        'default_sort_by', 'default_sort_order', 'auto_scan_on_startup',
        'enable_floating_window', 'items_per_page',
        'library_show_flat_view', 'library_show_tree_view',
        'pending_folder_name', 'pending_path', 'cloud_api_url',
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

        # 沙箱只使用启动器显式提供的库，旧 bootstrap 配置不能改写隔离边界。
        if not SANDBOX and config_path.exists():
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

    if settings.library_path:
        from backend.app.services.storage_migration import follow_library_redirect
        settings.library_path = follow_library_redirect(settings.library_path)

    # 显式沙箱库也恢复自己的存储位置配置，但不读取外部 bootstrap。
    if settings.library_path:
        own_config = Path(settings.library_path) / '.teamhub/config.json'
        if own_config.exists():
            _apply_config(settings, json.loads(own_config.read_text(encoding='utf-8')))

    # 初始化数据库
    # M1 修复:engine 在 import 时按默认路径创建;若上面恢复了 library_path,
    # 必须先重建引擎再建表,否则数据库落在错误的库位置
    from backend.app.database import rebind_engine
    rebind_engine()
    init_db()
    from backend.app.database import SessionLocal
    from backend.app.services.remote_update import recover_pending_updates
    with SessionLocal() as recovery_db:
        recover_pending_updates(recovery_db)

    from backend.app.services import partner_runtime

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
                        from backend.app.core.storage_paths import document_path
                        check_path = str(document_path(doc))
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

    from backend.app.services.indexing import start_worker, stop_worker
    from backend.app.services import preorganize, card_jobs
    start_worker()
    preorganize.start_worker()
    card_jobs.start_worker()
    try:
        yield
    finally:
        await card_jobs.stop_worker()
        await preorganize.stop_worker()
        await partner_runtime.shutdown()
        stop_worker()
        logger.info("TeamHub 关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="TeamHub 本地版",
    description="个人文件整理与版本管理工具",
    version="2.0.0",
    lifespan=lifespan
)

# CORS 中间件
@app.middleware('http')
async def storage_migration_guard(request, call_next):
    from backend.app.services import storage_migration
    from fastapi.responses import JSONResponse
    route = request.url.path
    if storage_migration.active and route != '/api/settings/storage/migration':
        return JSONResponse({'detail': '资料正在迁移，请稍候'}, status_code=423)
    if request.method not in {'GET', 'OPTIONS'} and route not in {
        '/api/settings/storage/recover', '/api/documents/search'
    }:
        journal = storage_migration.status()
        if journal and journal['phase'] not in {'complete', 'cancelled'}:
            return JSONResponse({'detail': '有未完成迁移，请先到设置处理迁移恢复'}, status_code=409)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# UTF-8 编码中间件
app.add_middleware(UTF8EncodingMiddleware)
# 最外层访问边界先于迁移检查与路由执行，阻断 CORS 无法拦截的简单请求。
app.add_middleware(LocalAccessMiddleware)

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
app.include_router(versions_router)
app.include_router(favorites_router)
app.include_router(sharing_router)
app.include_router(deliveries_router)
app.include_router(ai_router)
app.include_router(indexing_router)
app.include_router(preorganize_router)
app.include_router(document_cards_router)
app.include_router(partner_router)

# M4:共享服务错误信封透传(契约 §4)
from backend.app.api.sharing import register_cloud_error_handler
register_cloud_error_handler(app)


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "instance_id": os.environ.get("TEAMHUB_INSTANCE_ID"),
        "library_initialized": settings.library_path is not None,
        "library_path": settings.library_path,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
