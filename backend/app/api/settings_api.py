"""
设置 API 路由
包含文件库初始化、配置管理等
"""
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.database import get_db, init_db, save_library_config, load_library_config
from backend.app.models import Document, Version
from backend.app.config import settings
from backend.app.core.logger import get_logger
from backend.app.core.cloud_client import get_cloud_url, cloud_req, _http
from backend.app.api.schemas import LibrarySetup, SettingsUpdate, LibraryInfoResponse, MkdirRequest, PendingPathUpdate, RestoreRequest

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
    save_library_config(config)

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
    with open(bootstrap_path, 'w', encoding='utf-8') as f:
        json.dump(bootstrap_cfg, f, ensure_ascii=False, indent=2)
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
        "device_id": get_device_id(),
        "cloud_api_url": settings.cloud_api_url,
    }


@router.put("/config")
def update_settings(req: SettingsUpdate):
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
        settings.cloud_api_url = req.cloud_api_url.strip()

    # 持久化：跳过未提供的字段（None），保留 False/0 等有效值
    config = load_library_config()
    for k, v in req.model_dump().items():
        if v is not None:
            config[k] = v
    # bool 字段为 False 时 v is not None 也为 True，无需特殊处理
    # 但需确保 auto_scan_on_startup=False 被正确写入
    if req.auto_scan_on_startup is not None:
        config['auto_scan_on_startup'] = req.auto_scan_on_startup
    save_library_config(config)

    return {"message": "设置已更新"}


@router.post("/trash/clean")
def clean_trash_now(db: Session = Depends(get_db)):
    """立即清空回收站（永久删除所有 status='trashed' 文档）"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    import shutil
    library = Path(settings.library_path)
    trash_dir = library / '.teamhub' / 'trash'
    docs = db.query(Document).filter(Document.status == "trashed").all()
    deleted = 0

    for doc in docs:
        try:
            # 删除回收站中的物理文件
            if trash_dir.exists() and doc.storage_path:
                file_name = Path(doc.storage_path).name
                for item in trash_dir.iterdir():
                    parts = item.name.split('_', 1)
                    if len(parts) == 2 and parts[1] == file_name and parts[0].isdigit():
                        if item.is_dir():
                            shutil.rmtree(str(item))
                        else:
                            item.unlink()
            db.delete(doc)
            deleted += 1
        except Exception as e:
            logger.error(f"清理回收站文档 {doc.id} 失败: {e}")

    db.commit()
    logger.info(f"手动清空回收站，共删除 {deleted} 个文档")
    return {"deleted": deleted}


def _find_file_in_library(library: Path, filename: str, expected_size: int | None) -> Path | None:
    """
    在文件库中按文件名搜索，返回唯一匹配的路径。
    排除 .teamhub 系统目录。
    若找到多个候选则返回 None（无法自动判断）。
    """
    candidates = []
    for p in library.rglob(filename):
        # 跳过系统目录
        if ".teamhub" in p.parts:
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
      - 找到唯一匹配 → 更新 storage_path 和 Version.file_path（relocated）
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

        abs_path = library / doc.storage_path
        if abs_path.exists():
            continue  # 路径正常，无需处理

        # 路径失效：尝试按文件名在文件库内定位新路径
        filename = abs_path.name
        cur_ver = next((v for v in doc.versions if v.is_current), None)
        expected_size = cur_ver.file_size if cur_ver else None

        new_path = _find_file_in_library(library, filename, expected_size)
        if new_path:
            # 更新 storage_path 为新的相对路径
            try:
                new_rel = str(new_path.relative_to(library)).replace("\\", "/")
            except ValueError:
                new_rel = str(new_path)

            doc.storage_path = new_rel

            # 同步更新所有 Version.file_path（绝对路径）
            old_dir = abs_path.parent
            new_dir = new_path.parent
            if old_dir != new_dir:
                for ver in doc.versions:
                    if ver.file_path:
                        ver_path = Path(ver.file_path)
                        # 只更新与旧目录同根的版本文件路径
                        try:
                            rel = ver_path.relative_to(old_dir)
                            ver.file_path = str(new_dir / rel)
                        except ValueError:
                            pass  # 版本文件不在旧目录下，跳过

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
            check_path = str(library / doc.storage_path)
        else:
            continue
        if check_path and os.path.exists(check_path):
            storage = doc.storage_path or ""
            pfn = settings.pending_folder_name
            doc.status = "pending" if (storage.startswith(pfn + "/") or storage.startswith(pfn + "\\")) else "organized"
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


@router.get("/library/dirtree")
def get_library_dirtree(
    parent: str = Query(""),
    exclude: Optional[str] = Query(None),
):
    """
    返回文件库指定目录的直接子目录列表（懒加载模式，每次只返回一层）

    parent: 要列出子目录的相对路径（空字符串 = 文件库根目录）
    exclude: 排除指定相对路径及其子路径（防止文件夹整理时放入自身）
    自动排除 .teamhub 系统目录和待整理暂存区。
    """
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    library = Path(settings.library_path)
    EXCLUDED_NAMES = {'.teamhub', settings.pending_folder_name}

    target = library / parent if parent else library
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
        if norm_exclude and (rel_path == norm_exclude or rel_path.startswith(norm_exclude + "/")):
            continue
        items.append({
            "name": entry.name,
            "path": rel_path,
        })

    return {"items": items}


@router.put("/pending-path")
def update_pending_path(req: PendingPathUpdate, db: Session = Depends(get_db)):
    """
    修改待整理文件夹名称。
    migrate=True：将现有文件从旧目录移至新目录并更新数据库路径。
    migrate=False：仅清空数据库中的待整理记录，保留实体文件（不删除）。
    """
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    new_name = req.new_folder_name.strip()
    if not new_name:
        raise HTTPException(400, "文件夹名称不能为空")
    if "/" in new_name or "\\" in new_name:
        raise HTTPException(400, "文件夹名称不能包含路径分隔符")
    if new_name.startswith("."):
        raise HTTPException(400, "文件夹名称不能以 . 开头")

    old_name = settings.pending_folder_name
    if new_name == old_name:
        return {"message": "路径未发生变化", "pending_folder_name": new_name}

    library = Path(settings.library_path)
    old_dir = library / old_name
    new_dir = library / new_name

    if req.migrate:
        # 迁移：将现有待整理文件移至新目录，更新数据库记录
        new_dir.mkdir(exist_ok=True)
        pending_docs = db.query(Document).filter(Document.status == "pending").all()
        moved_count = 0

        for doc in pending_docs:
            if not doc.storage_path:
                continue
            old_file = library / doc.storage_path
            filename = Path(doc.storage_path).name
            new_file = new_dir / filename

            # 处理文件名冲突（新目录中已存在同名文件）
            if new_file.exists() and str(new_file.resolve()) != str(old_file.resolve()):
                stem = Path(filename).stem
                suffix = Path(filename).suffix
                counter = 1
                while new_file.exists():
                    new_file = new_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                if old_file.exists():
                    shutil.move(str(old_file), str(new_file))
                # 无论文件是否存在，都更新数据库中的路径（将旧名称前缀替换为新名称）
                doc.storage_path = str(new_file.relative_to(library)).replace("\\", "/")
                moved_count += 1
            except Exception as e:
                logger.error(f"迁移文件失败 {old_file}: {e}")

        db.commit()
        logger.info(f"待整理文件夹重命名: {old_name} → {new_name}，迁移 {moved_count} 个文档")
    else:
        # 不迁移：仅清空数据库记录，保留实体文件
        pending_docs = db.query(Document).filter(Document.status == "pending").all()
        deleted_count = len(pending_docs)
        for doc in pending_docs:
            db.delete(doc)
        db.commit()
        logger.info(f"清空待整理数据库记录（保留实体文件）：删除 {deleted_count} 条记录")

    # 更新运行时设置并持久化
    settings.pending_folder_name = new_name
    config = load_library_config()
    config["pending_folder_name"] = new_name
    save_library_config(config)

    return {
        "message": f"待整理路径已修改为「{new_name}」",
        "pending_folder_name": new_name,
        "migrate": req.migrate,
    }


@router.post("/library/mkdir")
def create_library_dir(req: MkdirRequest):
    """在文件库中创建目录"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    target = Path(settings.library_path) / req.path

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


# ===========================================================================
# 备份相关端点
# ===========================================================================

def _require_cloud_login(cfg: dict):
    """检查云服务登录态，未登录抛 400"""
    if not cfg.get("cloud_access_token"):
        raise HTTPException(400, "请先登录云服务后再执行备份操作")


def _call_cloud(method: str, path: str, cfg: dict, **kwargs):
    """调用云后端，非 2xx 时抛 HTTPException"""
    url = f"{get_cloud_url(cfg)}{path}"
    resp = cloud_req(method, url, cfg, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, f"云服务错误: {detail}")
    return resp.json()


@router.post("/backup/create")
def create_backup():
    """
    将 db.sqlite + config.json 打包为 zip，上传到云端 OSS。
    流程：
      1. 打包 .teamhub/db.sqlite + config.json 为临时 zip
      2. 调云后端 presign-upload 获取上传 URL + backup_id
      3. httpx PUT 直传 OSS
      4. 调云后端 confirm 确认上传
      5. 清理临时文件
    """
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    cfg = load_library_config()
    _require_cloud_login(cfg)

    teamhub_dir = Path(settings.library_path) / ".teamhub"
    db_file = teamhub_dir / "db.sqlite"
    cfg_file = teamhub_dir / "config.json"

    if not db_file.exists():
        raise HTTPException(400, "数据库文件不存在，无法备份")

    # 生成备份文件名
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"teamhub-backup-{ts}.zip"

    tmp_zip = None
    try:
        # 打包为临时 zip
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            tmp_zip = f.name

        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_file, "db.sqlite")
            if cfg_file.exists():
                zf.write(cfg_file, "config.json")

        file_size = os.path.getsize(tmp_zip)
        device_id = cfg.get("device_id", "")
        device_name = cfg.get("device_name", "")

        # 向云后端申请预签名上传 URL
        presign_data = _call_cloud(
            "POST", "/api/backup/presign-upload", cfg,
            json={
                "filename": filename,
                "file_size": file_size,
                "device_id": device_id,
                "device_name": device_name,
            },
        )
        backup_id = presign_data["backup_id"]
        upload_url = presign_data["upload_url"]

        # 直传 OSS（不经过云后端）
        with open(tmp_zip, "rb") as f:
            up_resp = _http.put(
                upload_url,
                content=f,
                headers={"Content-Type": "application/zip"},
                timeout=300,
            )
        if up_resp.status_code not in (200, 204):
            raise HTTPException(502, f"上传到 OSS 失败: HTTP {up_resp.status_code}")

        # 确认上传完成
        _call_cloud("POST", f"/api/backup/{backup_id}/confirm", cfg)

        logger.info(f"数据库备份成功: {filename}（{file_size} 字节）")
        return {"backup_id": backup_id, "filename": filename, "file_size": file_size}

    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            os.unlink(tmp_zip)


@router.get("/backup/list")
def list_backups():
    """列出当前用户在云端的所有备份（转发到云后端）"""
    cfg = load_library_config()
    _require_cloud_login(cfg)
    return _call_cloud("GET", "/api/backup/list", cfg)


@router.post("/backup/restore")
def restore_backup(req: RestoreRequest):
    """
    从云端恢复备份。
    - confirmed=False：仅检查本地是否有现有数据，返回 has_existing_data
    - confirmed=True：执行恢复（下载 zip，解压到 .teamhub/）
    恢复后需重启应用。
    """
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    cfg = load_library_config()
    _require_cloud_login(cfg)

    teamhub_dir = Path(settings.library_path) / ".teamhub"
    db_file = teamhub_dir / "db.sqlite"
    has_existing = db_file.exists() and db_file.stat().st_size > 0

    if not req.confirmed:
        # 仅检查，不执行
        return {"has_existing_data": has_existing}

    # 获取预签名下载 URL
    dl_data = _call_cloud(
        "GET", f"/api/backup/{req.backup_id}/presign-download", cfg
    )
    download_url = dl_data["download_url"]

    tmp_zip = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            tmp_zip = f.name

        # 下载备份 zip（使用外网 httpx，走系统代理）
        import httpx as _httpx
        with _httpx.Client(timeout=300) as client:
            with client.stream("GET", download_url) as resp:
                resp.raise_for_status()
                with open(tmp_zip, "wb") as out:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        out.write(chunk)

        # 解压到 .teamhub/（只还原 db.sqlite 和 config.json）
        teamhub_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()
            for name in ("db.sqlite", "config.json"):
                if name in names:
                    zf.extract(name, teamhub_dir)

        logger.info(f"数据库从云端备份恢复成功: backup_id={req.backup_id}")
        return {"success": True, "restart_required": True}

    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            os.unlink(tmp_zip)


@router.delete("/backup/{backup_id}")
def delete_backup(backup_id: str):
    """删除云端备份（转发到云后端）"""
    cfg = load_library_config()
    _require_cloud_login(cfg)
    return _call_cloud("DELETE", f"/api/backup/{backup_id}", cfg)
