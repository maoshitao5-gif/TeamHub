"""
设置 API 路由
包含文件库初始化、配置管理等
"""
import json
import os
import tempfile
from pathlib import Path

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.database import get_db, init_db, save_library_config, load_library_config, write_json_config
from backend.app.models import Document
from backend.app.config import settings
from backend.app.core.logger import get_logger
from backend.app.core.cloud_session import session_lock, refresh_lock
from backend.app.core.storage_paths import (
    document_path, pending_root, relative_location, classification_directory, classification_folder_roots,
)
from backend.app.services import storage_migration
from backend.app.api.schemas import LibrarySetup, SettingsUpdate, LibraryInfoResponse, MkdirRequest, PendingPathUpdate

logger = get_logger("api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/library", response_model=LibraryInfoResponse)
def get_library_info(db: Session = Depends(get_db)):
    """获取文件库信息"""
    initialized = settings.library_path is not None and Path(settings.library_path).exists()

    total_docs = 0
    total_size = 0
    pending_count = 0
    trash_count = 0
    trash_size = 0

    if initialized:
        total_docs = db.query(Document).filter(Document.status.in_(["organized", "pending"])).count()
        total_size = db.query(func.coalesce(func.sum(Document.total_size), 0)).filter(
            Document.status.in_(["organized", "pending"])
        ).scalar()
        pending_count = db.query(Document).filter(Document.status == "pending").count()
        trash_count = db.query(Document).filter(Document.status == "trashed").count()
        # 统计回收站目录大小
        trash_dir = Path(settings.library_path) / '.teamhub' / 'trash'
        if trash_dir.exists():
            for item in trash_dir.iterdir():
                try:
                    if item.is_file():
                        trash_size += item.stat().st_size
                    elif item.is_dir():
                        for f in item.rglob('*'):
                            if f.is_file():
                                trash_size += f.stat().st_size
                except OSError:
                    pass

    return LibraryInfoResponse(
        path=settings.library_path,
        initialized=initialized,
        total_documents=total_docs,
        total_size=total_size,
        pending_count=pending_count,
        trash_count=trash_count,
        trash_size=trash_size,
        pending_folder_name=settings.pending_folder_name,
        pending_path=str(pending_root()) if settings.library_path else None,
    )


@router.post("/library")
def setup_library(req: LibrarySetup):
    from backend.app.database import library_change, LibraryBusy, rebind_engine
    try:
        # 等在飞续期落盘后再切库，避免服务端已轮换而本地丢失新令牌。
        with refresh_lock, library_change(), session_lock:
            old_path = settings.library_path
            old_pending = settings.pending_path
            try:
                return _setup_library_locked(req)
            except Exception as exc:
                if settings.library_path != old_path:
                    settings.library_path = old_path
                    settings.pending_path = old_pending
                    try:
                        rebind_engine()
                        # 旧库已经初始化；回退只恢复伙伴状态，不重跑失败的目标迁移器。
                        import backend.app.database as database
                        from sqlalchemy import inspect
                        if inspect(database.engine).has_table('partner_tasks'):
                            from backend.app.services.partner_runtime import initialize
                            initialize()
                    except Exception as recovery_exc:
                        raise HTTPException(500, '切库失败，原位置配置已恢复，但旧库重新连接或任务恢复失败，请重新启动应用') from recovery_exc
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(500, f"切换文件库失败: {exc}") from exc
    except LibraryBusy as exc:
        raise HTTPException(409, str(exc)) from exc


def _setup_library_locked(req: LibrarySetup):
    """初始化文件库（首次设置或更换路径）"""
    path = Path(req.path)

    # 创建文件库目录
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"无法创建目录: {e}")

    # 检查目录可写
    # 独立临时文件探测可写性，不能覆盖用户已有的同名文件。
    try:
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b'test')
    except OSError as e:
        raise HTTPException(400, f"目录不可写: {e}")

    from backend.app.database import rebind_engine
    import backend.app.database as database
    settings.library_path = str(path.resolve())
    settings.pending_path = load_library_config().get("pending_path")
    try:
        if str(database.engine.url) != settings.database_url:
            rebind_engine()
        init_db()
    except Exception as exc:
        raise HTTPException(500, f"文件库初始化失败: {exc}") from exc

    # 保存配置到库目录
    config = load_library_config()
    config['library_path'] = settings.library_path
    config['default_storage_mode'] = settings.default_storage_mode
    config['trash_auto_clean_days'] = settings.trash_auto_clean_days
    config['max_file_size'] = settings.max_file_size
    config['on_conflict'] = settings.on_conflict
    config['default_sort_by'] = settings.default_sort_by
    config['default_sort_order'] = settings.default_sort_order
    config['auto_scan_on_startup'] = settings.auto_scan_on_startup
    config['items_per_page'] = settings.items_per_page
    config['enable_floating_window'] = settings.enable_floating_window
    save_library_config(config, strict=True)

    # 同时将 library_path 写入启动时读取的 bootstrap config，确保重启后能找到文件库
    if settings.is_electron:
        bootstrap_path = Path(settings.user_data_dir) / 'config' / 'config.json'
    else:
        bootstrap_path = Path('config.json')
    bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_cfg = {}
    if bootstrap_path.exists():
        try:
            with open(bootstrap_path, 'r', encoding='utf-8') as f:
                bootstrap_cfg = json.load(f)
        except Exception:
            pass
    bootstrap_cfg['library_path'] = settings.library_path
    from backend.app.database import write_json_config
    write_json_config(bootstrap_path, bootstrap_cfg)
    logger.info(f"Bootstrap config updated: {bootstrap_path}")

    # 切换文件库时清除 device_id 缓存，确保从新库的 config.json 读取
    from backend.app.core.device import reset_device_id_cache
    reset_device_id_cache()

    logger.info(f"Library initialized at: {settings.library_path}")
    return {"message": "文件库初始化成功", "path": settings.library_path}


@router.get("/config")
def get_settings():
    """获取当前设置"""
    from backend.app.core.device import get_device_id
    return {
        "library_path": settings.library_path,
        "default_storage_mode": settings.default_storage_mode,
        "trash_auto_clean_days": settings.trash_auto_clean_days,
        "max_file_size": settings.max_file_size,
        "on_conflict": settings.on_conflict,
        "default_sort_by": settings.default_sort_by,
        "default_sort_order": settings.default_sort_order,
        "auto_scan_on_startup": settings.auto_scan_on_startup,
        "items_per_page": settings.items_per_page,
        "enable_floating_window": settings.enable_floating_window,
        "library_show_flat_view": settings.library_show_flat_view,
        "library_show_tree_view": settings.library_show_tree_view,
        "pending_folder_name": settings.pending_folder_name,
        "pending_path": str(pending_root()) if settings.library_path else None,
        "device_id": get_device_id(),
        "cloud_api_url": settings.cloud_api_url,
    }


@router.put("/config")
def update_settings(req: SettingsUpdate, db: Session = Depends(get_db)):
    """更新设置"""
    if req.default_storage_mode is not None:
        settings.default_storage_mode = req.default_storage_mode
    if req.trash_auto_clean_days is not None:
        settings.trash_auto_clean_days = req.trash_auto_clean_days
    if req.max_file_size is not None:
        settings.max_file_size = req.max_file_size
    if req.on_conflict is not None:
        settings.on_conflict = req.on_conflict
    if req.default_sort_by is not None:
        settings.default_sort_by = req.default_sort_by
    if req.default_sort_order is not None:
        settings.default_sort_order = req.default_sort_order
    if req.auto_scan_on_startup is not None:
        settings.auto_scan_on_startup = req.auto_scan_on_startup
    if req.items_per_page is not None:
        settings.items_per_page = req.items_per_page
    if req.enable_floating_window is not None:
        settings.enable_floating_window = req.enable_floating_window
    if req.library_show_flat_view is not None:
        settings.library_show_flat_view = req.library_show_flat_view
    if req.library_show_tree_view is not None:
        settings.library_show_tree_view = req.library_show_tree_view
    if req.cloud_api_url is not None:
        from urllib.parse import urlsplit
        address = req.cloud_api_url.strip().rstrip('/')
        parsed = urlsplit(address)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(422, '云服务地址必须是有效的 HTTP(S) 地址，不含凭据、查询或片段')
        req.cloud_api_url = address

    # 持久化：跳过未提供的字段（None），保留 False/0 等有效值
    with session_lock:
        config_path = Path(settings.config_file)
        config = load_library_config()
        if Path(settings.config_file) != config_path:
            raise HTTPException(409, '个人库已变化，请重新保存设置')
        if req.cloud_api_url is not None and req.cloud_api_url != str(config.get('cloud_api_url', '')).rstrip('/'):
            for key in ('cloud_access_token', 'cloud_refresh_token', 'cloud_server_id', 'active_workspace_id', 'active_workspace_name'):
                config.pop(key, None)
        for k, v in req.model_dump().items():
            if v is not None:
                config[k] = v
        write_json_config(config_path, config)
        if req.cloud_api_url is not None:
            settings.cloud_api_url = req.cloud_api_url

    return {"message": "设置已更新"}


@router.post("/trash/clean")
def clean_trash_now(db: Session = Depends(get_db)):
    """立即清空回收站（永久删除所有 status='trashed' 文档）"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    from backend.app.services.document_storage import purge_documents
    docs = db.query(Document).filter(Document.status == "trashed").all()
    deleted = len(docs)
    purge_documents(db, docs)
    logger.info(f"手动清空回收站，共删除 {deleted} 个文档")
    return {"deleted": deleted}


def _find_file_in_library(library: Path, filename: str, expected_size: int | None, excluded=()) -> Path | None:
    """
    在文件库中按文件名搜索，返回唯一匹配的路径。
    排除 .teamhub 系统目录。
    若找到多个候选则返回 None（无法自动判断）。
    """
    candidates = []
    for p in library.rglob(filename):
        # 跳过系统目录
        if ".teamhub" in p.parts or p.is_symlink() or any(p.resolve().is_relative_to(root) for root in excluded):
            continue
        # 用文件大小二次验证，过滤同名但内容不同的文件
        if expected_size is not None:
            try:
                if p.stat().st_size != expected_size:
                    continue
            except OSError:
                continue
        candidates.append(p)
    return candidates[0] if len(candidates) == 1 else None


@router.post("/scan")
def scan_library(db: Session = Depends(get_db)):
    """
    扫描文件库：检查文档物理路径，自动修复因文件夹重命名导致的路径失效。
    - organized/pending → 路径失效时，先尝试按文件名在文件库内重新定位
      - 找到唯一匹配 → 更新 storage_path，保持版本快照不变（relocated）
      - 找不到 → 标记 missing
    - missing → organized/pending（文件路径已恢复）
    """
    if not settings.library_path:
        return {"message": "文件库未初始化", "scanned": 0, "missing": 0, "restored": 0, "relocated": 0}

    library = Path(settings.library_path)

    # --- 正向：organized/pending → 重定位 or missing ---
    docs = db.query(Document).filter(Document.status.in_(["organized", "pending"])).all()
    missing_count = 0
    relocated_count = 0

    for doc in docs:
        if doc.storage_mode == "index":
            check_path = doc.original_path
            if check_path and not os.path.exists(check_path):
                doc.status = "missing"
                missing_count += 1
            continue

        if not doc.storage_path:
            continue

        abs_path = document_path(doc)
        if abs_path.exists():
            continue  # 路径正常，无需处理

        # 路径失效：尝试按文件名在文件库内定位新路径
        filename = abs_path.name
        cur_ver = next((v for v in doc.versions if v.id == doc.current_version_id), None)
        expected_size = cur_ver.file_size if cur_ver else None

        own_root = pending_root() if doc.storage_area == "pending" else library.resolve()
        other_root = library.resolve() if doc.storage_area == "pending" else pending_root()
        excluded = [other_root] if other_root.is_relative_to(own_root) else []
        excluded += [document_path(other) for other in docs if other.id != doc.id and other.storage_path]
        new_path = _find_file_in_library(own_root, filename, expected_size, excluded)
        if new_path:
            # 更新 storage_path 为新的相对路径
            try:
                new_rel = relative_location(new_path, doc.storage_area)
            except ValueError:
                new_rel = str(new_path)

            doc.storage_path = new_rel

            relocated_count += 1
        else:
            doc.status = "missing"
            missing_count += 1

    # --- 反向：missing → 恢复 organized/pending ---
    missing_docs = db.query(Document).filter(Document.status == "missing").all()
    restored_count = 0
    for doc in missing_docs:
        if doc.storage_mode == "index":
            check_path = doc.original_path
        elif doc.storage_path:
            check_path = str(document_path(doc))
        else:
            continue
        if check_path and os.path.exists(check_path):
            doc.status = "pending" if doc.storage_area == "pending" else "organized"
            restored_count += 1

    db.commit()
    return {
        "message": "扫描完成",
        "scanned": len(docs) + len(missing_docs),
        "missing": missing_count,
        "restored": restored_count,
        "relocated": relocated_count,
    }


@router.get("/library/subdirs")
def list_library_subdirs(parent: str = "", db: Session = Depends(get_db)):
    """列出文件库中的子目录（用于前端选择存放位置）"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    library = Path(settings.library_path)
    folder_roots = classification_folder_roots(db)
    target = classification_directory(parent, db, allow_root=True, folder_roots=folder_roots)

    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "目录不存在")

    dirs = []
    for item in sorted(target.iterdir()):
        if item.is_dir() and not item.name.startswith('.'):
            relative = str(item.relative_to(library.resolve()))
            try:
                classification_directory(relative, db, folder_roots=folder_roots)
            except HTTPException:
                continue
            dirs.append({
                "name": item.name,
                "path": relative,
            })

    return {"dirs": dirs, "current": parent}


@router.get("/library/dirtree")
def get_library_dirtree(
    parent: str = Query(""),
    exclude: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    返回文件库指定目录的直接子目录列表（懒加载模式，每次只返回一层）

    parent: 要列出子目录的相对路径（空字符串 = 文件库根目录）
    exclude: 排除指定相对路径及其子路径（防止文件夹整理时放入自身）
    自动排除 .teamhub 系统目录和待整理暂存区。
    """
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    EXCLUDED_NAMES = {'.teamhub', settings.pending_folder_name}

    folder_roots = classification_folder_roots(db)
    target = classification_directory(parent, db, allow_root=True, folder_roots=folder_roots)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "目录不存在")

    norm_exclude = exclude.replace('\\', '/') if exclude else None

    items = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return {"items": []}

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name.startswith('.') or entry.name in EXCLUDED_NAMES:
            continue
        rel_path = f"{parent}/{entry.name}".lstrip("/") if parent else entry.name
        try:
            classification_directory(rel_path, db, folder_roots=folder_roots)
        except HTTPException:
            continue
        if norm_exclude and (rel_path == norm_exclude or rel_path.startswith(norm_exclude + "/")):
            continue
        items.append({
            "name": entry.name,
            "path": rel_path,
        })

    return {"items": items}


@router.put("/pending-path")
def update_pending_path(req: PendingPathUpdate):
    # 旧客户端也必须走同一迁移边界，禁止再以改路径为由清空资料登记。
    if not req.migrate:
        raise HTTPException(400, "路径调整必须迁移已登记资料，不能清空记录")
    name = req.new_folder_name.strip()
    if not name or Path(name).is_absolute() or '..' in Path(name).parts or '/' in name or '\\' in name or name.startswith('.'):
        raise HTTPException(400, '请输入有效子目录名称；独立路径请使用存储迁移')
    return _migration_call(storage_migration.migrate, 'pending', str(Path(settings.library_path) / name))


def _migration_call(fn, *args):
    from backend.app.database import LibraryBusy
    try:
        return fn(*args)
    except LibraryBusy as exc:
        raise HTTPException(409, str(exc)) from exc


from pydantic import BaseModel
from typing import Literal

class StorageMigrationRequest(BaseModel):
    area: Literal['library', 'pending']
    path: str


@router.post('/storage/preview')
def preview_storage(req: StorageMigrationRequest):
    return _migration_call(storage_migration.preview, req.area, req.path)


@router.post('/storage/migrate')
def migrate_storage(req: StorageMigrationRequest):
    return _migration_call(storage_migration.migrate, req.area, req.path)


@router.get('/storage/migration')
def storage_migration_status():
    journal = storage_migration.status()
    if not journal:
        return None
    return {key: journal.get(key) for key in ('id', 'phase', 'area', 'error', 'warnings', 'old_library', 'new_library', 'old_pending', 'new_pending')}


@router.post('/storage/recover')
def recover_storage():
    return _migration_call(storage_migration.recover)


@router.post("/library/mkdir")
def create_library_dir(req: MkdirRequest, db: Session = Depends(get_db)):
    """在文件库中创建目录"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    target = classification_directory(req.path, db)

    # 安全检查：防止路径穿越
    try:
        target.resolve().relative_to(Path(settings.library_path).resolve())
    except ValueError:
        raise HTTPException(400, "无效路径：不能超出文件库范围")

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"创建目录失败: {e}")

    return {"message": "目录创建成功", "path": req.path}


# 兼容旧客户端：云数据库备份已停用，恢复只能使用停写后的维护脚本。
@router.post("/backup/create")
@router.get("/backup/list")
@router.post("/backup/restore")
@router.delete("/backup/{backup_id}")
def retired_cloud_backup():
    from backend.app.core.decommissioned import gone_response
    return gone_response("请使用运维指引中的停写备份与恢复维护脚本。")
