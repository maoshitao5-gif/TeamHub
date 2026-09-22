"""
文档 API 路由
包含文档的收纳、搜索、更新、删除等操作
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func, or_

from backend.app.database import get_db
from backend.app.models import Document, Version, Tag
from backend.app.config import settings
from backend.app.core.file_utils import calculate_sha256, get_document_or_404
from backend.app.core.logger import get_logger
from backend.app.core.changelog import record_change
from backend.app.api.schemas import (
    DocumentCreate, DocumentUpdate, DocumentOrganize, DocumentQuickAdd,
    BatchOrganize, BatchTagUpdate, BatchDelete, BatchRestore,
    SearchRequest, DocumentRelocate,
    DocumentResponse, DocumentListResponse, PendingCountResponse,
    TagResponse, VersionResponse,
    PendingAnalysisResponse, SimilarGroup, SimilarGroupDocument, TagSuggestion,
    DuplicateCheckRequest, DuplicateCheckResponse,
    FolderDuplicateCheckRequest, FolderDuplicateCheckResponse, DuplicateDetail
)

logger = get_logger("api.documents")
router = APIRouter(prefix="/api/documents", tags=["documents"])


def _delete_doc_files(doc: Document) -> None:
    """永久删除文档的所有物理文件：主文件 + 所有历史版本文件。
    index 模式不删除主文件（文件不归 TeamHub 所有）。
    版本文件只删除位于文件库内部的文件（避免误删库外原始文件）。
    """
    if not settings.library_path:
        return
    library_path = Path(settings.library_path)

    # 主文件（index 模式跳过）
    if doc.storage_mode != "index" and doc.storage_path:
        main_path = library_path / doc.storage_path
        try:
            if main_path.is_dir():
                shutil.rmtree(str(main_path), ignore_errors=True)
            elif main_path.exists():
                main_path.unlink()
        except Exception as e:
            logger.warning(f"删除主文件失败 {main_path}: {e}")

    # 各历史版本文件（file_path 是绝对路径，只删除库内文件）
    for ver in doc.versions:
        if not ver.file_path:
            continue
        ver_path = Path(ver.file_path)
        try:
            ver_path.relative_to(library_path)
        except ValueError:
            continue  # 文件在文件库外，跳过
        try:
            if ver_path.is_dir():
                shutil.rmtree(str(ver_path), ignore_errors=True)
            elif ver_path.exists():
                ver_path.unlink()
        except Exception as e:
            logger.warning(f"删除版本文件失败 {ver_path}: {e}")


def _get_or_create_tags(db: Session, tag_names: list[str]) -> list[Tag]:
    """获取或创建标签列表"""
    tags = []
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = db.query(Tag).filter(Tag.name == name).first()
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        tags.append(tag)
    return tags


def _build_document_response(doc: Document) -> dict:
    """构建文档响应数据"""
    current_version = None
    for v in doc.versions:
        if v.is_current and not v.relative_path:
            current_version = v
            break

    tags = [TagResponse(id=t.id, name=t.name, color=t.color, document_count=0) for t in doc.tags]

    resp = {
        "id": doc.id,
        "name": doc.name,
        "description": doc.description,
        "is_folder": doc.is_folder,
        "storage_path": doc.storage_path,
        "storage_mode": doc.storage_mode,
        "original_path": doc.original_path,
        "status": doc.status,
        "file_count": doc.file_count,
        "total_size": doc.total_size,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "tags": tags,
        "current_version": None,
        "sync_status": doc.sync_status,
        "cloud_sync_enabled": doc.cloud_sync_enabled,
        "last_synced_at": doc.last_synced_at,
        "local_modified": doc.local_modified,
        "cloud_updated_at": doc.cloud_updated_at,
        "cloud_source": doc.cloud_source,
        "cloud_doc_id": doc.cloud_doc_id,
        "cloud_pushed_at": doc.cloud_pushed_at,
        "cloud_hash": doc.cloud_hash,
        "workspace_id": doc.workspace_id,
    }

    if current_version:
        resp["current_version"] = VersionResponse(
            id=current_version.id,
            version_number=current_version.version_number,
            file_path=current_version.file_path,
            relative_path=current_version.relative_path,
            sha256_hash=current_version.sha256_hash,
            file_size=current_version.file_size,
            original_filename=current_version.original_filename,
            note=current_version.note,
            is_current=current_version.is_current,
            created_at=current_version.created_at
        )

    return resp


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


def _count_folder(folder_path: str) -> tuple[int, int]:
    """统计文件夹内的文件数和总大小"""
    file_count = 0
    total_size = 0
    for root, dirs, files in os.walk(folder_path):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if not f.startswith('.'):
                file_count += 1
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return file_count, total_size


@router.post("", response_model=DocumentResponse)
def create_document(
    req: DocumentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """收纳文档（单文件或文件夹）"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化，请先设置文件库路径")
    
    # 验证标签必填
    if not req.tags or len(req.tags) == 0:
        raise HTTPException(400, "标签是必填项，请至少添加一个标签")

    source = Path(req.source_path)
    if not source.exists():
        raise HTTPException(400, f"源路径不存在: {req.source_path}")

    library = Path(settings.library_path)
    # 使用请求参数中的 is_folder（前端已经判断过是否为文件夹）
    # 如果前端未提供或为 False，则自动检测
    is_folder = req.is_folder if req.is_folder else source.is_dir()

    # 确定目标路径
    if req.target_dir:
        target_base = library / req.target_dir
    else:
        target_base = library
    target_base.mkdir(parents=True, exist_ok=True)
    target_path = target_base / source.name

    # 存储路径（相对于文件库）
    storage_rel = str(target_path.relative_to(library))

    # 执行文件操作
    if req.storage_mode == "move":
        if target_path.exists():
            raise HTTPException(400, f"目标位置已存在同名文件: {target_path.name}")
        shutil.move(str(source), str(target_path))
    elif req.storage_mode == "copy":
        if target_path.exists():
            raise HTTPException(400, f"目标位置已存在同名文件: {target_path.name}")
        if is_folder:
            shutil.copytree(str(source), str(target_path))
        else:
            shutil.copy2(str(source), str(target_path))
    elif req.storage_mode == "index":
        # 仅索引模式不移动文件
        storage_rel = None

    # 计算大小和文件数
    if is_folder:
        actual_path = target_path if req.storage_mode != "index" else source
        file_count, total_size = _count_folder(str(actual_path))
    else:
        file_count = 1
        actual_path = target_path if req.storage_mode != "index" else source
        total_size = os.path.getsize(str(actual_path)) if actual_path.exists() else 0

    # 创建文档记录
    doc = Document(
        name=req.name,
        description=req.description,
        is_folder=is_folder,
        storage_path=storage_rel,
        storage_mode=req.storage_mode,
        original_path=str(source),
        status="organized",
        file_count=file_count,
        total_size=total_size,
        cloud_source=req.cloud_source,
        cloud_doc_id=req.cloud_doc_id,  # 从云仓库导入时关联云端 doc_id
    )
    db.add(doc)
    db.flush()

    # 处理标签
    if req.tags:
        doc.tags = _get_or_create_tags(db, req.tags)

    # 创建 v1 版本（单文件文档）
    if not is_folder:
        file_path_str = str(target_path) if req.storage_mode != "index" else str(source)
        version = Version(
            document_id=doc.id,
            version_number=1,
            file_path=file_path_str,
            sha256_hash=None,  # 后台计算
            file_size=total_size,
            original_filename=source.name,
            is_current=True
        )
        db.add(version)
        db.flush()
        background_tasks.add_task(_compute_hash_background, version.id, file_path_str)

    record_change(db, "document", doc.id, "create", {
        "name": doc.name, "status": doc.status, "storage_mode": doc.storage_mode,
        "total_size": doc.total_size, "tag_ids": [t.id for t in doc.tags]
    })
    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.post("/quick-add", response_model=DocumentResponse)
def quick_add_to_pending(
    req: DocumentQuickAdd,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """快速放入待整理区（不需要打标签和分类）"""
    
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    source = Path(req.source_path)
    if not source.exists():
        raise HTTPException(400, f"源路径不存在: {req.source_path}")

    library = Path(settings.library_path)
    pending_dir = library / settings.pending_folder_name
    pending_dir.mkdir(exist_ok=True)

    # 直接使用前端传入的 is_folder 参数（前端已经判断过是否为文件夹）
    is_folder = req.is_folder
    
    target = pending_dir / source.name

    # 避免重名
    if target.exists():
        if is_folder:
            # 文件夹：直接使用名称加计数器
            counter = 1
            while target.exists():
                target = pending_dir / f"{source.name}_{counter}"
                counter += 1
        else:
            # 文件：使用 stem 和 suffix
            stem = source.stem
            suffix = source.suffix
            counter = 1
            while target.exists():
                target = pending_dir / f"{stem}_{counter}{suffix}"
                counter += 1

    # 移动到待整理区
    delete_failed = False
    try:
        if is_folder:
            # 文件夹：先尝试使用 move，如果失败则使用 copytree + rmtree 的组合
            try:
                # 先尝试直接移动（更快）
                # 注意：在 Windows 上，如果目标文件夹已存在，shutil.move 会尝试将源文件夹内容移动到目标文件夹中
                # 这可能导致问题，所以我们需要先检查目标是否存在
                if target.exists():
                    # 目标已存在，不能直接移动，需要使用复制+删除的方式
                    raise OSError(f"目标文件夹已存在: {target}")
                shutil.move(str(source), str(target))
            except (PermissionError, OSError) as move_err:
                # 移动失败，尝试复制+删除的方式
                logger.info(f"直接移动失败，尝试复制+删除方式: {source}, 错误: {move_err}")
                
                # 在复制之前，再次检查目标是否存在
                # 如果存在，先删除（可能是之前的操作留下的空文件夹）
                if target.exists():
                    try:
                        # 检查目标文件夹是否为空
                        if any(target.iterdir()):
                            # 目标文件夹不为空，不能删除，需要重新计算目标路径
                            logger.warning(f"目标文件夹已存在且不为空: {target}，重新计算目标路径")
                            # 重新计算目标路径（添加计数器）
                            counter = 1
                            while target.exists():
                                target = pending_dir / f"{source.name}_{counter}"
                                counter += 1
                        else:
                            # 目标文件夹为空，可以删除
                            target.rmdir()
                            logger.info(f"删除了空的目标文件夹: {target}")
                    except Exception as cleanup_err:
                        logger.warning(f"清理目标文件夹失败: {target}, 错误: {cleanup_err}")
                        # 如果清理失败，重新计算目标路径
                        counter = 1
                        while target.exists():
                            target = pending_dir / f"{source.name}_{counter}"
                            counter += 1
                
                try:
                    shutil.copytree(str(source), str(target), dirs_exist_ok=False)
                    # 复制成功后，尝试删除源文件夹
                    try:
                        shutil.rmtree(str(source))
                    except (PermissionError, OSError) as del_err:
                        # 如果删除失败，记录警告但继续（文件已复制到目标位置）
                        delete_failed = True
                        logger.warning(f"复制成功但删除源文件夹失败: {source}, 错误: {del_err}")
                except (PermissionError, OSError) as copy_err:
                    # 复制也失败，抛出详细错误
                    error_info = {
                        "error_type": type(copy_err).__name__,
                        "error_message": str(copy_err),
                        "winerror": getattr(copy_err, 'winerror', None),
                        "errno": getattr(copy_err, 'errno', None),
                    }
                    logger.error(f"复制文件夹失败: {error_info}")
                    
                    # Windows错误5：访问被拒绝
                    if hasattr(copy_err, 'winerror') and copy_err.winerror == 5:
                        error_msg = (
                            f"无法移动文件夹：访问被拒绝。\n\n"
                            f"文件夹内可能包含被其他程序锁定的文件（如 .git 目录）。\n\n"
                            f"请关闭所有可能使用该文件夹的程序（如 Git、文件资源管理器等），然后重试。\n\n"
                            f"文件夹：{source.name}"
                        )
                        raise HTTPException(400, error_msg)
                    # Windows错误32：文件被其他进程占用
                    elif hasattr(copy_err, 'winerror') and copy_err.winerror == 32:
                        error_msg = (
                            f"无法移动文件夹：文件夹正在被其他程序使用。\n\n"
                            f"请关闭所有打开该文件夹的程序，然后重试。\n\n"
                            f"文件夹：{source.name}"
                        )
                        raise HTTPException(400, error_msg)
                    # Windows错误183：文件已存在
                    elif hasattr(copy_err, 'winerror') and copy_err.winerror == 183:
                        # 目标文件夹已存在，尝试重新计算目标路径
                        logger.warning(f"目标文件夹已存在（WinError 183），重新计算目标路径: {target}")
                        counter = 1
                        while target.exists():
                            target = pending_dir / f"{source.name}_{counter}"
                            counter += 1
                        # 再次尝试复制
                        try:
                            shutil.copytree(str(source), str(target), dirs_exist_ok=False)
                            # 复制成功后，尝试删除源文件夹
                            try:
                                shutil.rmtree(str(source))
                            except (PermissionError, OSError) as del_err:
                                delete_failed = True
                                logger.warning(f"复制成功但删除源文件夹失败: {source}, 错误: {del_err}")
                        except (PermissionError, OSError) as retry_err:
                            # 重试也失败，抛出错误
                            error_msg = (
                                f"无法移动文件夹：目标位置已存在同名文件夹，且重试后仍失败。\n\n"
                                f"错误：{str(retry_err)}\n\n"
                                f"文件夹：{source.name}\n\n"
                                f"请确保目标位置没有同名文件夹，或者手动清理后重试。"
                            )
                            raise HTTPException(400, error_msg)
                    # 其他权限或系统错误
                    else:
                        error_msg = (
                            f"无法移动文件夹：{str(copy_err)}\n\n"
                            f"文件夹：{source.name}\n\n"
                            f"请确保文件夹未被其他程序使用，并且您有足够的权限。"
                        )
                        raise HTTPException(400, error_msg)
        else:
            # 文件：直接使用 move，并处理权限错误
            try:
                shutil.move(str(source), str(target))
            except (PermissionError, OSError) as move_err:
                error_info = {
                    "error_type": type(move_err).__name__,
                    "error_message": str(move_err),
                    "winerror": getattr(move_err, 'winerror', None),
                    "errno": getattr(move_err, 'errno', None),
                }
                logger.error(f"移动文件失败: {error_info}")
                # Windows错误5：访问被拒绝
                if hasattr(move_err, 'winerror') and move_err.winerror == 5:
                    error_msg = (
                        f"无法移动文件：访问被拒绝。\n\n"
                        f"文件可能被其他程序锁定。\n\n"
                        f"请关闭所有可能使用该文件的程序，然后重试。\n\n"
                        f"文件：{source.name}"
                    )
                    raise HTTPException(400, error_msg)
                # Windows错误32：文件被其他进程占用
                elif hasattr(move_err, 'winerror') and move_err.winerror == 32:
                    error_msg = (
                        f"无法移动文件：文件正在被其他程序使用。\n\n"
                        f"请关闭所有打开该文件的程序，然后重试。\n\n"
                        f"文件：{source.name}"
                    )
                    raise HTTPException(400, error_msg)
                # 其他权限或系统错误
                error_msg = (
                    f"无法移动文件：{str(move_err)}\n\n"
                    f"文件：{source.name}\n\n"
                    f"请确保文件未被其他程序使用，并且您有足够的权限。"
                )
                raise HTTPException(400, error_msg)
    except HTTPException:
        # 重新抛出 HTTPException（已经包含详细错误信息）
        raise
    except Exception as e:
        raise HTTPException(500, f"移动文件失败: {str(e)}")

    # 计算大小
    if is_folder:
        file_count, total_size = _count_folder(str(target))
    else:
        file_count = 1
        total_size = os.path.getsize(str(target))

    storage_rel = str(target.relative_to(library))

    doc = Document(
        name=source.name,
        is_folder=is_folder,
        storage_path=storage_rel,
        storage_mode="move",
        original_path=str(source),
        status="pending",
        file_count=file_count,
        total_size=total_size,
    )
    db.add(doc)
    try:
        db.commit()   # 先提交文档，确保 doc.id 已生成并持久化
        db.refresh(doc)
    except Exception as db_err:
        # 数据库提交失败 → 将文件移回原位，避免文件丢失
        try:
            shutil.move(str(target), str(source))
            logger.info(f"[quick-add] 数据库提交失败，已将文件移回原位: {target} -> {source}")
        except Exception as rollback_err:
            logger.error(f"[quick-add] 数据库提交失败且文件回滚也失败！文件滞留在: {target}, 错误: {rollback_err}")
        raise HTTPException(500, f"添加到待整理失败: {db_err}")

    # 变更日志单独提交（非关键，失败不影响文档创建）
    try:
        record_change(db, "document", doc.id, "create", {"name": doc.name, "status": "pending"})
        db.commit()
    except Exception as e:
        logger.warning(f"[quick-add] 变更日志记录失败（不影响主操作）: {e}")
        db.rollback()

    # 为非文件夹文件创建 Version 记录（查重依赖 Version.sha256_hash）
    # 同步计算哈希，确保下次拖入相同文件时能立即命中查重
    if not is_folder:
        try:
            sha256 = calculate_sha256(str(target))
        except Exception as hash_err:
            logger.warning(f"[quick-add] 哈希计算失败，后台重试: {target}, 错误: {hash_err}")
            sha256 = None
        version = Version(
            document_id=doc.id,
            version_number=1,
            file_path=str(target),
            sha256_hash=sha256,
            file_size=total_size,
            original_filename=source.name,
            is_current=True,
        )
        db.add(version)
        db.commit()
        # 如果同步计算失败，后台补算
        if sha256 is None:
            background_tasks.add_task(_compute_hash_background, version.id, str(target))

    if delete_failed:
        logger.warning(f"文件夹已复制到目标位置，但删除源文件夹失败: {source} -> {target}")

    return _build_document_response(doc)


@router.post("/{doc_id}/undo-pending")
def undo_pending(doc_id: str, db: Session = Depends(get_db)):
    """
    撤销放入待整理操作：将文件移回原始路径并删除文档记录
    仅支持 status=pending 且 storage_mode=move 的文档
    """
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    doc = get_document_or_404(db, doc_id)
    if doc.status != "pending":
        raise HTTPException(400, "只能撤销待整理状态的文档")
    if doc.storage_mode != "move":
        raise HTTPException(400, "只能撤销移动模式的文档")
    if not doc.original_path:
        raise HTTPException(400, "无法获取原始路径，无法撤销")

    library = Path(settings.library_path)
    source = library / doc.storage_path  # 当前在待整理区的位置
    target = Path(doc.original_path)     # 原始位置

    if not source.exists():
        # 文件已不存在于待整理区，仅删除数据库记录
        db.delete(doc)
        db.commit()
        return {"message": "文件已不存在，已删除记录", "original_path": str(target)}

    if target.exists():
        raise HTTPException(400, f"原始位置已存在同名文件，无法撤销：{target}")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except Exception as e:
        raise HTTPException(500, f"撤销失败：{e}")

    db.delete(doc)
    db.commit()
    logger.info(f"撤销待整理：{source} -> {target}")
    return {"message": "已撤销，文件已移回原始位置", "original_path": str(target)}


def _do_duplicate_check(sha256: str, exclude_doc_id: Optional[str], db) -> DuplicateCheckResponse:
    """
    公共查重逻辑：按 sha256 在 organized / pending 中匹配，可排除指定文档 ID。
    """
    # 1. 优先检查已整理文档库（organized）
    q = (
        db.query(Version)
        .join(Document, Version.document_id == Document.id)
        .filter(
            Version.sha256_hash == sha256,
            Version.is_current == True,
            Document.status == "organized",
            Document.is_folder == False,
        )
    )
    if exclude_doc_id:
        q = q.filter(Document.id != exclude_doc_id)
    existing_version = q.first()
    if existing_version is not None:
        existing_doc = db.query(Document).filter(Document.id == existing_version.document_id).first()
        if existing_doc:
            logger.info(f"[查重] 发现重复(organized): '{existing_doc.name}' (id={existing_doc.id})")
            return DuplicateCheckResponse(
                is_duplicate=True,
                existing_document=DocumentResponse(**_build_document_response(existing_doc)),
                sha256_hash=sha256,
                check_succeeded=True,
            )

    # 2. 检查待整理区（pending）
    pq = (
        db.query(Version)
        .join(Document, Version.document_id == Document.id)
        .filter(
            Version.sha256_hash == sha256,
            Version.is_current == True,
            Document.status == "pending",
            Document.is_folder == False,
        )
    )
    if exclude_doc_id:
        pq = pq.filter(Document.id != exclude_doc_id)
    pending_version = pq.first()
    if pending_version is not None:
        pending_doc = db.query(Document).filter(Document.id == pending_version.document_id).first()
        if pending_doc:
            logger.info(f"[查重] 发现重复(pending): '{pending_doc.name}' (id={pending_doc.id})")
            return DuplicateCheckResponse(
                is_duplicate=True,
                duplicate_in_pending=True,
                pending_document=DocumentResponse(**_build_document_response(pending_doc)),
                sha256_hash=sha256,
                check_succeeded=True,
            )

    return DuplicateCheckResponse(is_duplicate=False, sha256_hash=sha256, check_succeeded=True)


@router.post("/check-duplicate", response_model=DuplicateCheckResponse)
def check_duplicate(
    req: DuplicateCheckRequest,
    db: Session = Depends(get_db)
):
    """
    单文件查重：支持两种输入模式
    - file_path：从文件路径计算 SHA-256（上传/整理前）
    - sha256_hash：直接传入已知哈希（云端导入时用已缓存值）
    可通过 exclude_doc_id 排除自身（推送时排查其他重复）
    """
    try:
        if req.sha256_hash:
            sha256 = req.sha256_hash
        elif req.file_path:
            source = Path(req.file_path)
            if not source.exists() or source.is_dir():
                return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)
            sha256 = calculate_sha256(req.file_path)
        else:
            return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)
    except Exception as e:
        logger.warning(f"[查重] SHA-256 计算失败，fallthrough: {e}")
        return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)

    try:
        return _do_duplicate_check(sha256, req.exclude_doc_id, db)
    except Exception as e:
        logger.warning(f"[查重] 数据库查询失败，fallthrough: {e}")
        return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)


@router.get("/{document_id}/check-library-duplicate", response_model=DuplicateCheckResponse)
def check_library_duplicate(
    document_id: str,
    db: Session = Depends(get_db)
):
    """
    按文档 ID 查重：取该文档当前版本的 sha256，检查是否存在其他文档与其内容重复。
    主要用途：
    - 待整理整理前：检查同内容文件是否已在文档库
    - 推送到云端前：检查是否有其他本地文档与当前文档内容相同
    排除自身（exclude_doc_id = document_id）。
    """
    get_document_or_404(db, document_id)

    current_ver = (
        db.query(Version)
        .filter(Version.document_id == document_id, Version.is_current == True)
        .first()
    )
    if not current_ver:
        return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)

    # sha256 可能尚未计算完成（后台任务延迟）
    sha256 = current_ver.sha256_hash
    if not sha256:
        # 尝试即时计算（文件存在时）
        fpath = current_ver.file_path
        if fpath and not Path(fpath).is_absolute() and settings.library_path:
            fpath = str(Path(settings.library_path) / fpath)
        if fpath and Path(fpath).exists() and not Path(fpath).is_dir():
            try:
                sha256 = calculate_sha256(fpath)
                current_ver.sha256_hash = sha256
                db.commit()
            except Exception:
                pass
        if not sha256:
            return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)

    try:
        return _do_duplicate_check(sha256, document_id, db)
    except Exception as e:
        logger.warning(f"[查重] 数据库查询失败，fallthrough: {e}")
        return DuplicateCheckResponse(is_duplicate=False, check_succeeded=False)


@router.post("/check-folder-duplicates", response_model=FolderDuplicateCheckResponse)
def check_folder_duplicates(
    req: FolderDuplicateCheckRequest,
    db: Session = Depends(get_db)
):
    """
    文件夹（不打散模式）查重：递归枚举文件夹内所有文件，批量比对已整理文档的 Version 哈希
    - 返回具体重复文件详情列表（source_filename + 已有文档信息），供前端展示跳转入口
    - 使用 IN 批量查询，仅一次数据库往返
    - 任何异常均返回 check_succeeded=False（前端 fallthrough）
    """
    try:
        folder = Path(req.folder_path)
        if not folder.exists() or not folder.is_dir():
            return FolderDuplicateCheckResponse(check_succeeded=False)

        # 递归枚举所有文件，计算 SHA-256，建立 hash -> 文件路径 映射（去重取第一个）
        hash_to_file: dict[str, str] = {}
        total_files = 0
        for root, dirs, files in os.walk(str(folder)):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    h = calculate_sha256(fpath)
                    if h not in hash_to_file:
                        hash_to_file[h] = fpath
                except Exception:
                    pass  # 单文件 IO 失败不影响整体
                total_files += 1

        if not hash_to_file:
            return FolderDuplicateCheckResponse(total_files=total_files, duplicate_count=0, check_succeeded=True)

        # 批量 IN 查询，获取 organized 文档中匹配的版本及文档信息
        matched_rows = (
            db.query(Version, Document)
            .join(Document, Version.document_id == Document.id)
            .filter(
                Version.sha256_hash.in_(list(hash_to_file.keys())),
                Version.is_current == True,
                Document.status == "organized",
            )
            .all()
        )

        # 构建 hash -> (doc_id, doc_name, doc_status) 映射
        hash_to_doc: dict[str, tuple] = {}
        for version, doc in matched_rows:
            h = version.sha256_hash
            if h not in hash_to_doc:
                hash_to_doc[h] = (doc.id, doc.name, doc.status)

        # 构建重复文件详情列表
        duplicates: list[DuplicateDetail] = []
        for h, fpath in hash_to_file.items():
            if h in hash_to_doc:
                doc_id, doc_name, doc_status = hash_to_doc[h]
                try:
                    rel = str(Path(fpath).relative_to(folder))
                except Exception:
                    rel = Path(fpath).name
                duplicates.append(DuplicateDetail(
                    source_filename=rel,
                    existing_document_id=doc_id,
                    existing_document_name=doc_name,
                    existing_document_status=doc_status,
                ))

        # 检查是否存在哈希未完成计算的已整理文档（has_pending_hashes）
        has_pending_hashes = bool(
            (db.query(func.count(Version.id))
             .join(Document, Version.document_id == Document.id)
             .filter(
                 Version.is_current == True,
                 Version.sha256_hash.is_(None),
                 Document.status == "organized",
             )
             .scalar()) or 0
        )

        duplicate_count = len(duplicates)
        logger.info(f"[文件夹查重] {folder.name}: 共 {total_files} 文件，{duplicate_count} 个重复")
        return FolderDuplicateCheckResponse(
            total_files=total_files,
            duplicate_count=duplicate_count,
            duplicates=duplicates,
            has_pending_hashes=has_pending_hashes,
            check_succeeded=True
        )
    except Exception as e:
        logger.warning(f"[文件夹查重] 失败，fallthrough: {req.folder_path}, 错误: {e}")
        return FolderDuplicateCheckResponse(check_succeeded=False)


@router.post("/search", response_model=DocumentListResponse)
def search_documents(req: SearchRequest, db: Session = Depends(get_db)):
    """搜索文档"""
    query = db.query(Document)

    # 状态筛选：如果明确指定了状态则按状态过滤，否则默认排除回收站
    if req.status:
        query = query.filter(Document.status == req.status)
    else:
        query = query.filter(Document.status != "trashed")

    # 关键词搜索
    if req.keyword:
        kw = f"%{req.keyword}%"
        query = query.filter(or_(Document.name.ilike(kw), Document.description.ilike(kw)))

    # 标签筛选
    if req.tags:
        for tag_name in req.tags:
            query = query.filter(Document.tags.any(Tag.name == tag_name))

    # 类型筛选
    if req.is_folder is not None:
        query = query.filter(Document.is_folder == req.is_folder)

    # 目录过滤
    if req.folder is not None:
        if req.folder == '.':
            # 根目录：storage_path 不含 "/" 或 "\"（直接在库根目录下的文档）
            query = query.filter(
                Document.storage_path.isnot(None),
                ~Document.storage_path.contains('/'),
                ~Document.storage_path.contains('\\'),
            )
        else:
            # 指定子目录：storage_path 以该目录为前缀（包含所有层级的子目录文档）
            prefix_slash = req.folder.replace('\\', '/') + '/'
            prefix_back = req.folder.replace('/', '\\') + '\\'
            query = query.filter(
                or_(
                    Document.storage_path.startswith(prefix_slash),
                    Document.storage_path.startswith(prefix_back),
                )
            )

    # 总数
    total = query.count()

    # 排序
    sort_column = getattr(Document, req.sort_by, Document.updated_at)
    if req.sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # 分页
    offset = (req.page - 1) * req.page_size
    docs = query.options(selectinload(Document.versions)).offset(offset).limit(req.page_size).all()

    return DocumentListResponse(
        documents=[_build_document_response(d) for d in docs],
        total=total,
        page=req.page,
        page_size=req.page_size
    )


@router.get("/pending/count", response_model=PendingCountResponse)
def get_pending_count(db: Session = Depends(get_db)):
    """获取待整理文档数量"""
    count = db.query(Document).filter(Document.status == "pending").count()
    return PendingCountResponse(count=count)


@router.get("/dirs")
def get_document_dirs(parent: str = "", db: Session = Depends(get_db)):
    """
    从数据库中提取已整理文档的目录层级。
    返回指定父路径下的直接子目录（不递归），仅含有文档的目录。
    parent="" 表示获取根目录下的直接子目录。
    """
    rows = db.query(Document.storage_path).filter(
        Document.status == 'organized',
        Document.storage_path.isnot(None),
        Document.storage_path != '',
    ).all()

    dirs = set()
    for row in rows:
        path = row[0]  # 使用索引访问，避免 SQLAlchemy 版本差异导致的元组解包问题
        if not path:
            continue
        # 统一为正斜杠
        normalized = path.replace('\\', '/')
        parts = normalized.split('/')
        if len(parts) < 2:
            continue  # 根目录文件，无目录部分

        # 去掉文件名（最后一段），只看目录前缀
        dir_parts = parts[:-1]

        if parent == "":
            # 获取根目录下的直接子目录
            dirs.add(dir_parts[0])
        else:
            # 获取指定 parent 下的直接子目录
            parent_normalized = parent.replace('\\', '/')
            parent_parts = parent_normalized.split('/')
            depth = len(parent_parts)
            if (len(dir_parts) > depth and
                    dir_parts[:depth] == parent_parts):
                dirs.add('/'.join(dir_parts[:depth + 1]))

    # 过滤掉磁盘上实际不存在的目录（文件夹已从磁盘删除时不再显示）
    library_root = Path(settings.library_path) if settings.library_path else None
    items = sorted([
        {"name": d.split('/')[-1], "path": d}
        for d in dirs
        if library_root is None or (library_root / d).is_dir()
    ], key=lambda x: x["name"])

    return {"items": items}


@router.post("/pending/analyze", response_model=PendingAnalysisResponse)
def analyze_pending(db: Session = Depends(get_db)):
    """智能分析待整理文档：识别相似版本 + 标签建议"""
    from backend.app.core.name_analyzer import group_by_similarity, suggest_tags

    docs = db.query(Document).filter(Document.status == "pending").all()
    if not docs:
        return PendingAnalysisResponse(version_groups=[], tag_suggestions=[])

    # 版本分组
    groups = group_by_similarity(docs)
    version_groups = []
    for g in groups:
        group_docs = [
            SimilarGroupDocument(
                id=d.id, name=d.name, total_size=d.total_size, created_at=d.created_at
            ) for d in g['documents']
        ]
        version_groups.append(SimilarGroup(
            base_name=g['base_name'],
            documents=group_docs,
            suggested_name=g['suggested_name']
        ))

    # 标签建议
    tag_suggestions_raw = suggest_tags(docs)
    tag_suggestions = [
        TagSuggestion(tag=s['tag'], document_ids=s['document_ids'], reason=s['reason'])
        for s in tag_suggestions_raw
    ]

    return PendingAnalysisResponse(
        version_groups=version_groups,
        tag_suggestions=tag_suggestions
    )


# 批量操作路由必须在路径参数路由之前定义，避免路由匹配冲突
@router.post("/batch/organize")
def batch_organize_documents(req: BatchOrganize, db: Session = Depends(get_db)):
    """批量整理文档"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")
    
    # 验证标签必填
    if not req.tags or len(req.tags) == 0:
        raise HTTPException(400, "标签是必填项，请至少添加一个标签")

    # 查询所有匹配的文档
    all_docs = db.query(Document).filter(
        Document.id.in_(req.document_ids)
    ).all()

    # 只筛选状态为 pending 的文档
    docs = [d for d in all_docs if d.status == "pending"]

    if not docs:
        # 检查是否有文档存在但状态不对
        missing_ids = set(req.document_ids) - {d.id for d in all_docs}
        wrong_status_ids = {d.id for d in all_docs if d.status != "pending"}
        
        if missing_ids:
            error_msg = f"文档不存在: {', '.join(missing_ids)}"
        elif wrong_status_ids:
            error_msg = f"以下文档不是待整理状态，无法批量整理: {', '.join(wrong_status_ids)}"
        else:
            error_msg = "未找到待整理文档"
        
        raise HTTPException(404, error_msg)

    library = Path(settings.library_path)
    target_dir = library / req.target_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    tags = _get_or_create_tags(db, req.tags) if req.tags else []
    organized_count = 0

    for doc in docs:
        current_path = library / doc.storage_path if doc.storage_path else None
        
        # 计算目标路径（无论文件是否存在，都需要更新 storage_path）
        # 始终使用实际文件的扩展名，避免 doc.name 中的后缀与物理文件不一致
        if not doc.is_folder and current_path and current_path.exists() and current_path.suffix:
            target_path = target_dir / (Path(doc.name).stem + current_path.suffix)
        else:
            target_path = target_dir / doc.name
        
        # 如果文件存在，移动文件；如果不存在，只更新路径
        if current_path and current_path.exists():
            if target_path.exists():
                logger.warning(f"目标位置已存在: {target_path}，跳过文档 {doc.id}")
                continue

            shutil.move(str(current_path), str(target_path))

            # 更新版本的文件路径
            for v in doc.versions:
                if v.is_current and not v.relative_path:
                    v.file_path = str(target_path)

        # 无论文件是否存在，都更新 storage_path 到目标位置
        doc.storage_path = str(target_path.relative_to(library))
        doc.status = "organized"
        if tags:
            doc.tags = tags
        record_change(db, "document", doc.id, "update", {"status": "organized", "target_dir": req.target_dir})
        organized_count += 1

    db.commit()

    # 批量整理后为已整理文档生成指针文件（尽力而为，失败不影响响应）
    try:
        from backend.app.core.pointer import generate_pointer
        for doc in docs:
            if doc.status == "organized" and doc.storage_path:
                sha256_hex = generate_pointer(settings.library_path, doc, doc.storage_path)
                if sha256_hex:
                    for v in doc.versions:
                        if v.is_current and not v.relative_path:
                            v.sha256_hash = sha256_hex
                            break
        db.commit()
    except Exception as e:
        logger.warning(f"[批量整理] 指针生成异常: {e}")

    return {"message": f"已整理 {organized_count} 个文档"}


@router.get("/autocomplete")
def autocomplete_documents(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db)
):
    """文档名称自动补全，用于搜索框实时联想"""
    if not q or not q.strip():
        return []
    docs = db.query(Document).filter(
        Document.name.ilike(f"%{q}%"),
        Document.status == "organized"
    ).order_by(Document.name).limit(limit).all()
    return [{"id": d.id, "name": d.name, "is_folder": d.is_folder} for d in docs]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, db: Session = Depends(get_db)):
    """获取文档详情"""
    doc = get_document_or_404(db, document_id)
    return _build_document_response(doc)


@router.get("/{document_id}/files")
def get_document_files(document_id: str, db: Session = Depends(get_db)):
    """获取文件夹文档的内容树"""
    doc = get_document_or_404(db, document_id)
    if not doc.is_folder:
        raise HTTPException(400, "该文档不是文件夹类型")

    # 确定文件夹的物理路径
    folder_path = None
    if doc.storage_path and settings.library_path:
        folder_path = Path(settings.library_path) / doc.storage_path
    elif doc.original_path:
        folder_path = Path(doc.original_path)

    if not folder_path or not folder_path.exists():
        return []

    def build_tree(dir_path: Path) -> list:
        items = []
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return items

        for entry in entries:
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                children = build_tree(entry)
                items.append({
                    'name': entry.name,
                    'type': 'directory',
                    'children': children,
                    'path': str(entry),
                })
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                items.append({
                    'name': entry.name,
                    'type': 'file',
                    'size': size,
                    'path': str(entry),
                })
        return items

    return build_tree(folder_path)


@router.put("/{document_id}", response_model=DocumentResponse)
def update_document(document_id: str, req: DocumentUpdate, db: Session = Depends(get_db)):
    """更新文档信息（名称、描述）"""
    doc = get_document_or_404(db, document_id)

    if req.name is not None:
        doc.name = req.name
    if req.description is not None:
        doc.description = req.description

    record_change(db, "document", doc.id, "update", {"name": doc.name, "description": doc.description})
    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.put("/{document_id}/tags")
def update_document_tags(document_id: str, tag_names: list[str], db: Session = Depends(get_db)):
    """设置文档的标签（替换）"""
    doc = get_document_or_404(db, document_id)

    doc.tags = _get_or_create_tags(db, tag_names)
    record_change(db, "document", doc.id, "update", {"tag_names": tag_names})
    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.post("/{document_id}/organize", response_model=DocumentResponse)
def organize_document(document_id: str, req: DocumentOrganize, db: Session = Depends(get_db)):
    """整理文档：从待整理区移入文件库指定位置"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")
    
    # 验证标签必填
    if not req.tags or len(req.tags) == 0:
        raise HTTPException(400, "标签是必填项，请至少添加一个标签")

    doc = get_document_or_404(db, document_id)
    if doc.status != "pending":
        raise HTTPException(400, "该文档不在待整理状态")

    library = Path(settings.library_path)
    target_dir = library / req.target_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    # 当前文件位置
    current_path = library / doc.storage_path if doc.storage_path else None
    
    # 计算目标路径（无论文件是否存在，都需要更新 storage_path）
    new_name = req.name or doc.name
    
    # 如果是文件，需要处理扩展名
    if not doc.is_folder and current_path and current_path.exists():
        # 获取原文件的扩展名
        original_suffix = current_path.suffix
        # 检查用户输入的新名称是否包含扩展名
        new_name_path = Path(new_name)
        if new_name_path.suffix:
            # 用户输入的名称已包含扩展名，使用用户输入的扩展名
            target_path = target_dir / new_name
        else:
            # 用户输入的名称不包含扩展名，保留原扩展名
            target_path = target_dir / f"{new_name}{original_suffix}"
            # 更新 new_name 以包含扩展名，确保 doc.name 也包含扩展名
            new_name = f"{new_name}{original_suffix}"
    else:
        # 文件夹或文件不存在，直接使用新名称
        target_path = target_dir / new_name

    # 如果文件存在，移动文件；如果不存在，只更新路径
    if current_path and current_path.exists():
        if target_path.exists():
            raise HTTPException(400, f"目标位置已存在: {target_path.name}")

        shutil.move(str(current_path), str(target_path))

        # 更新版本的文件路径
        for v in doc.versions:
            if v.is_current and not v.relative_path:
                v.file_path = str(target_path)
    # 无论文件是否存在，都更新 storage_path 到目标位置
    new_storage_path = str(target_path.relative_to(library))
    doc.storage_path = new_storage_path

    # 更新文档名称：使用最终的文件名（包含扩展名）
    if req.name:
        # new_name 已经在处理扩展名时更新过了，直接使用
        doc.name = new_name
    doc.status = "organized"
    if req.tags:
        doc.tags = _get_or_create_tags(db, req.tags)

    record_change(db, "document", doc.id, "update", {
        "status": "organized", "target_dir": req.target_dir
    })
    db.commit()
    db.refresh(doc)

    # 整理成功后生成指针文件（尽力而为，失败不影响响应）
    try:
        from backend.app.core.pointer import generate_pointer
        sha256_hex = generate_pointer(settings.library_path, doc, doc.storage_path)
        if sha256_hex:
            for v in doc.versions:
                if v.is_current and not v.relative_path:
                    v.sha256_hash = sha256_hex
                    break
            db.commit()
    except Exception as e:
        logger.warning(f"[整理] 指针生成异常: doc_id={doc.id}, 错误: {e}")

    return _build_document_response(doc)


@router.post("/{document_id}/move-to-pending", response_model=DocumentResponse)
def move_to_pending(document_id: str, db: Session = Depends(get_db)):
    """将文档库中的文档移回待整理区，方便二次整理"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    doc = get_document_or_404(db, document_id)
    if doc.status != "organized":
        raise HTTPException(400, "只有已整理的文档才能移回待整理区")

    library = Path(settings.library_path)
    pending_dir = library / settings.pending_folder_name
    pending_dir.mkdir(exist_ok=True)

    # 当前文件位置
    current_path = library / doc.storage_path if doc.storage_path else None
    
    # 计算目标路径（待整理目录）
    target_path = pending_dir / doc.name
    
    # 如果是文件，保留原扩展名
    if not doc.is_folder and current_path and current_path.exists() and not target_path.suffix:
        target_path = target_path.with_suffix(current_path.suffix)
    
    # 避免重名
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while target_path.exists():
            target_path = pending_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    # 如果文件存在，移动文件；如果不存在，只更新路径
    if current_path and current_path.exists():
        if target_path.exists():
            raise HTTPException(400, f"目标位置已存在: {target_path.name}")

        logger.info(f"尝试移动文件: {current_path} -> {target_path}")
        try:
            shutil.move(str(current_path), str(target_path))
            logger.info(f"文件移动成功: {target_path}")

            # 更新版本的文件路径
            for v in doc.versions:
                if v.is_current and not v.relative_path:
                    v.file_path = str(target_path)
        except (PermissionError, OSError) as e:
            # 记录详细错误信息
            error_info = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "winerror": getattr(e, 'winerror', None),
                "errno": getattr(e, 'errno', None),
                "filename": getattr(e, 'filename', None),
            }
            logger.error(f"文件移动失败: {error_info}")
            
            # Windows错误32：文件被其他进程占用
            if hasattr(e, 'winerror') and e.winerror == 32:
                error_msg = (
                    f"无法移动文件：文件正在被其他程序使用。\n\n"
                    f"请关闭所有打开该文件的程序（如压缩软件、文件资源管理器等），然后重试。\n\n"
                    f"文件：{doc.name}"
                )
                raise HTTPException(400, error_msg)
            # 其他权限或系统错误
            error_msg = (
                f"无法移动文件：{str(e)}\n\n"
                f"文件：{doc.name}\n\n"
                f"请确保文件未被其他程序使用，并且您有足够的权限。"
            )
            raise HTTPException(400, error_msg)

    # 无论文件是否存在，都更新 storage_path 到待整理区
    # 如果文件移动失败（被锁定），上面的异常会阻止执行到这里
    new_storage_path = str(target_path.relative_to(library))
    doc.storage_path = new_storage_path
    doc.status = "pending"

    db.commit()
    db.refresh(doc)
    
    logger.info(f"文档 {doc.id} ({doc.name}) 已移回待整理区")
    
    return _build_document_response(doc)


@router.delete("/{document_id}")
def delete_document(document_id: str, permanent: bool = False, db: Session = Depends(get_db)):
    """删除文档（默认移入回收站，permanent=true 永久删除）"""
    doc = get_document_or_404(db, document_id)

    if permanent or doc.status == "trashed":
        # 永久删除：清理所有实体文件（主文件 + 各版本文件），再删数据库记录
        _delete_doc_files(doc)
        # 同时清理回收站副本（若有）
        if doc.status == "trashed" and doc.storage_path and settings.library_path:
            trash_dir = Path(settings.library_path) / ".teamhub" / "trash"
            if trash_dir.exists():
                file_name = Path(doc.storage_path).name
                for item in trash_dir.iterdir():
                    parts = item.name.split("_", 1)
                    if len(parts) == 2 and parts[1] == file_name and parts[0].isdigit():
                        try:
                            shutil.rmtree(str(item)) if item.is_dir() else item.unlink()
                        except Exception as e:
                            logger.warning(f"删除回收站文件失败 {item}: {e}")
        record_change(db, "document", doc.id, "delete", {"name": doc.name})
        db.delete(doc)
    else:
        # 软删除：移入回收站
        from datetime import datetime, timezone
        if doc.storage_path and settings.library_path:
            library = Path(settings.library_path)
            source = library / doc.storage_path
            trash_dir = library / '.teamhub' / 'trash'
            trash_dir.mkdir(parents=True, exist_ok=True)

            import time
            trash_name = f"{int(time.time())}_{source.name}"
            trash_path = trash_dir / trash_name

            if source.exists():
                shutil.move(str(source), str(trash_path))

        doc.status = "trashed"
        doc.trashed_at = datetime.now(timezone.utc)
        record_change(db, "document", doc.id, "trash", {"name": doc.name, "trashed_at": doc.trashed_at})

    db.commit()
    return {"message": "已删除" if permanent else "已移入回收站"}


@router.post("/{document_id}/restore")
def restore_document(document_id: str, db: Session = Depends(get_db)):
    """从回收站恢复文档"""
    doc = db.query(Document).filter(Document.id == document_id, Document.status == "trashed").first()
    if not doc:
        raise HTTPException(404, "文档不存在或不在回收站中")

    # 从 .teamhub/trash/ 恢复文件到原位置
    if doc.storage_path and settings.library_path:
        library = Path(settings.library_path)
        trash_dir = library / '.teamhub' / 'trash'
        target_path = library / doc.storage_path

        # 在回收站中查找对应文件（格式: {timestamp}_{filename}）
        restored = False
        if trash_dir.exists():
            file_name = Path(doc.storage_path).name
            # 查找匹配的回收站文件（按时间戳降序，取最新的）
            candidates = []
            for item in trash_dir.iterdir():
                # 匹配 {timestamp}_{原文件名} 格式
                parts = item.name.split('_', 1)
                if len(parts) == 2 and parts[1] == file_name and parts[0].isdigit():
                    candidates.append(item)

            if candidates:
                # 取最新的（时间戳最大的）
                candidates.sort(key=lambda p: p.name, reverse=True)
                trash_file = candidates[0]

                # 确保目标目录存在
                target_path.parent.mkdir(parents=True, exist_ok=True)

                if target_path.exists():
                    raise HTTPException(400, f"原位置已存在同名文件: {target_path.name}，请先处理冲突")

                shutil.move(str(trash_file), str(target_path))
                restored = True

        if not restored:
            logger.warning(f"回收站中未找到文档 {doc.id} 的物理文件，仅恢复数据库状态")

    doc.status = "organized"
    doc.trashed_at = None
    record_change(db, "document", doc.id, "restore", {"name": doc.name, "status": "organized"})
    db.commit()
    return {"message": "已恢复"}


@router.post("/{document_id}/relocate", response_model=DocumentResponse)
def relocate_document(document_id: str, req: DocumentRelocate, db: Session = Depends(get_db)):
    """重新定位缺失文档的文件路径"""
    doc = get_document_or_404(db, document_id)
    if doc.status != "missing":
        raise HTTPException(400, "只有缺失状态的文档才能重新定位")

    new_path = Path(req.new_path)
    if not new_path.exists():
        raise HTTPException(400, f"新路径不存在: {req.new_path}")

    library = Path(settings.library_path) if settings.library_path else None

    # 更新路径
    if library and str(new_path).startswith(str(library)):
        doc.storage_path = str(new_path.relative_to(library))
    else:
        doc.storage_path = None
    doc.original_path = str(new_path)
    doc.status = "organized"

    # 更新版本的文件路径
    for v in doc.versions:
        if v.is_current and not v.relative_path:
            v.file_path = str(new_path)

    db.commit()
    db.refresh(doc)
    logger.info(f"文档 {doc.id} 已重新定位到: {new_path}")
    return _build_document_response(doc)


@router.post("/batch/tags")
def batch_update_tags(req: BatchTagUpdate, db: Session = Depends(get_db)):
    """批量更新标签"""
    docs = db.query(Document).filter(Document.id.in_(req.document_ids)).all()
    if not docs:
        raise HTTPException(404, "未找到指定文档")

    add_tags = _get_or_create_tags(db, req.add_tags)
    remove_tag_names = set(n.strip() for n in req.remove_tags if n.strip())

    for doc in docs:
        # 添加标签
        existing_names = {t.name for t in doc.tags}
        for tag in add_tags:
            if tag.name not in existing_names:
                doc.tags.append(tag)
        # 移除标签
        if remove_tag_names:
            doc.tags = [t for t in doc.tags if t.name not in remove_tag_names]

    db.commit()
    return {"message": f"已更新 {len(docs)} 个文档的标签"}


@router.post("/batch/delete")
def batch_delete_documents(req: BatchDelete, db: Session = Depends(get_db)):
    """批量永久删除文档"""
    docs = db.query(Document).filter(Document.id.in_(req.document_ids)).all()
    if not docs:
        raise HTTPException(404, "未找到指定文档")

    deleted_count = 0
    for doc in docs:
        # 清理所有实体文件（主文件 + 各版本文件）
        _delete_doc_files(doc)
        # 同时清理回收站副本（若有）
        if doc.status == "trashed" and doc.storage_path and settings.library_path:
            trash_dir = Path(settings.library_path) / ".teamhub" / "trash"
            if trash_dir.exists():
                file_name = Path(doc.storage_path).name
                for item in trash_dir.iterdir():
                    parts = item.name.split("_", 1)
                    if len(parts) == 2 and parts[1] == file_name and parts[0].isdigit():
                        try:
                            shutil.rmtree(str(item)) if item.is_dir() else item.unlink()
                        except Exception as e:
                            logger.warning(f"删除回收站文件失败 {item}: {e}")
        record_change(db, "document", doc.id, "delete", {"name": doc.name})
        db.delete(doc)
        deleted_count += 1

    db.commit()
    return {"message": f"已永久删除 {deleted_count} 个文档"}


@router.post("/batch/restore")
def batch_restore_documents(req: BatchRestore, db: Session = Depends(get_db)):
    """批量恢复文档"""
    docs = db.query(Document).filter(
        Document.id.in_(req.document_ids),
        Document.status == "trashed"
    ).all()
    if not docs:
        raise HTTPException(404, "未找到可恢复的文档")

    restored_count = 0
    for doc in docs:
        # 尝试从回收站恢复文件
        if doc.storage_path and settings.library_path:
            library = Path(settings.library_path)
            trash_dir = library / '.teamhub' / 'trash'
            target_path = library / doc.storage_path

            if trash_dir.exists():
                file_name = Path(doc.storage_path).name
                candidates = []
                for item in trash_dir.iterdir():
                    parts = item.name.split('_', 1)
                    if len(parts) == 2 and parts[1] == file_name and parts[0].isdigit():
                        candidates.append(item)

                if candidates:
                    candidates.sort(key=lambda p: p.name, reverse=True)
                    trash_file = candidates[0]
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    if not target_path.exists():
                        shutil.move(str(trash_file), str(target_path))

        doc.status = "organized"
        doc.trashed_at = None
        record_change(db, "document", doc.id, "restore", {"name": doc.name})
        restored_count += 1

    db.commit()
    return {"message": f"已恢复 {restored_count} 个文档"}


