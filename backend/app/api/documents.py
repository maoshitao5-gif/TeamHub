"""
文档 API 路由
包含文档的收纳、搜索、更新、删除等操作
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from backend.app.database import get_db
from backend.app.models import Document, Version, Tag
from backend.app.config import settings
from backend.app.core.file_utils import calculate_sha256, get_document_or_404, find_trash_entries
from backend.app.core.logger import get_logger
from backend.app.core.changelog import record_change
from backend.app.services import versions as version_service
from backend.app.services.document_responses import (
    build_document_response as _build_document_response, build_document_responses,
)
from backend.app.api.schemas import (
    DocumentCreate, DocumentUpdate, DocumentOrganize, DocumentQuickAdd, DocumentFolderMove,
    BatchOrganize, BatchTagUpdate, BatchDelete, BatchRestore,
    SearchRequest, SearchMatch, DocumentRelocate,
    DocumentResponse, DocumentListResponse, PendingCountResponse,
    VersionResponse,
    PendingAnalysisResponse, SimilarGroup, SimilarGroupDocument, TagSuggestion,
    DuplicateCheckRequest, DuplicateCheckResponse,
    FolderDuplicateCheckRequest, FolderDuplicateCheckResponse, DuplicateDetail
)

from backend.app.core.storage_paths import (
    classification_directory, document_path, pending_root, relative_location, working_location,
)
from backend.app.services.document_storage import (
    owned_working_file as _owned_working_file, purge_documents as _purge_documents,
)

from backend.app.services.preorganize_storage import (
    working_changes as _working_changes, organize_working as _organize_working,
    get_or_create_tags as _get_or_create_tags,
)

logger = get_logger("api.documents")
router = APIRouter(prefix="/api/documents", tags=["documents"])


def _trash_entry_candidates(trash_dir: Path, doc: Document) -> list[Path]:
    """回收站条目定位:M2 起条目名为文档 ID(按 ID 精确匹配);
    旧文件名格式无法确定归属，不自动匹配"""
    return find_trash_entries(trash_dir, doc.id, doc.storage_path)


def _restore_working(doc: Document, move) -> None:
    if doc.storage_path and doc.storage_mode != "index":
        trash = Path(settings.library_path) / ".teamhub" / "trash"
        candidates = _trash_entry_candidates(trash, doc)
        if not candidates:
            raise HTTPException(409, "未找到按资料 ID 保存的回收站文件，请核对副本后恢复")
        move(candidates[0], document_path(doc))
    doc.status = "pending" if doc.storage_area == "pending" else "organized"
    doc.trashed_at = None


def _working_location(relative_path: str, area='library') -> Path:
    return working_location(relative_path, area)



def _compute_hash_background(version_id: str, file_path: str, library_path: str):
    """后台任务固定所属库；在途计算持有会话，切库不能穿过此操作。"""
    from backend.app.database import SessionLocal
    db = SessionLocal()
    try:
        expected_db = Path(library_path) / '.teamhub' / 'db.sqlite'
        if Path(db.bind.url.database).resolve() != expected_db.resolve():
            logger.warning("跳过已切换文件库的旧后台摘要任务")
            return
        if os.path.isfile(file_path):
            sha256 = calculate_sha256(file_path)
            version = db.query(Version).filter(Version.id == version_id).first()
            if version:
                version.sha256_hash = sha256
                db.commit()
    except Exception as exc:
        logger.error(f"Background hash calculation failed: {exc}")
    finally:
        db.close()


def _ingest_document(req: DocumentCreate, db: Session, *, pending: bool = False):
    """普通收录与快速收录共用查重、快照及提交边界。"""
    from backend.app.services import snapshots

    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")
    if req.storage_mode == "move" and not pending:
        raise HTTPException(400, "移动收录仅用于拖入待整理")
    source = Path(req.source_path)
    if not source.exists() or source.is_symlink():
        raise HTTPException(400, "源路径不存在或为符号链接")
    library = Path(settings.library_path).resolve()
    if req.storage_mode == "move":
        source = source.resolve()
        if source.is_relative_to(library) or library.is_relative_to(source):
            raise HTTPException(400, "不能移动个人库内的资料或包含个人库的目录，请使用库内整理功能")
    is_folder = source.is_dir()
    target_base = (pending_root() if pending
                   else classification_directory(req.target_dir or "", db, allow_root=True))
    if (not pending and not target_base.is_relative_to(library)) or target_base.is_relative_to(library / ".teamhub"):
        raise HTTPException(400, "目标目录必须位于文件库的工作目录内")
    if is_folder and target_base.is_relative_to(source.resolve()):
        raise HTTPException(400, "不能把文件夹复制到自身内部")
    target = target_base / source.name
    created_working = False
    try:
        with _working_changes(db) as move:
            # 先捕获资料内容，再据此查重与生成工作副本，避免两次读源文件不一致。
            if is_folder:
                captured = snapshots.store_folder_snapshot(library, source)
            else:
                key, digest, size = snapshots.store_file_snapshot(library, source)
                captured = (key, digest, None, size)
            existing = (db.query(Version).join(Document, Version.document_id == Document.id)
                        .filter(Version.sha256_hash == captured[1], Document.status != "trashed",
                                Document.is_folder == is_folder).first())
            if existing:
                existing_doc = db.get(Document, existing.document_id)
                version_data = VersionResponse.model_validate(existing).model_dump(mode="json")
                version_data["is_current"] = existing_doc.current_version_id == existing.id
                return JSONResponse({
                    "error": {"code": "DUPLICATE_CONTENT", "message": "相同内容已收录", "details": None},
                    "document": jsonable_encoder(_build_document_response(existing_doc)),
                    "version": version_data,
                })
            if req.storage_mode in {"copy", "move"}:
                target_base.mkdir(parents=True, exist_ok=True)
                if pending:
                    counter = 1
                    while target.exists() or target.is_symlink():
                        target = target_base / f"{source.stem}_{counter}{source.suffix}"
                        counter += 1
                if target.exists() or target.is_symlink():
                    raise HTTPException(400, f"目标位置已存在同名文件: {target.name}")
                if req.storage_mode == "move":
                    move(source, target)
                    # 对移动后的实际内容建首版；保存失败由事务补偿恢复原路径。
                    if is_folder:
                        captured = snapshots.store_folder_snapshot(library, target)
                    else:
                        key, digest, size = snapshots.store_file_snapshot(library, target)
                        captured = (key, digest, None, size)
                else:
                    snapshots.export_object(library, captured[0], target.name, target_base, is_folder)
                    created_working = True
            file_count, total_size = (snapshots.folder_statistics(snapshots.resolve_object(library, captured[0]))
                                      if is_folder else (1, captured[3]))
            doc = Document(
                name=req.name, description=req.description, is_folder=is_folder,
                doc_type="folder" if is_folder else "archive" if source.suffix.lower() in {".zip", ".rar", ".7z", ".gz"} else "file",
                storage_path=relative_location(target, "pending" if pending else "library") if req.storage_mode in {"copy", "move"} else None,
                storage_area="pending" if pending else "library",
                storage_mode=req.storage_mode, original_path=str(source),
                status="pending" if pending else "organized", file_count=file_count, total_size=total_size,
                cloud_source=req.cloud_source, cloud_doc_id=req.cloud_doc_id,
            )
            db.add(doc)
            db.flush()
            if not pending:
                doc.tags = _get_or_create_tags(db, req.tags)
            version_service.snapshot_working_copy(db, doc, prepared_snapshot=captured)
            record_change(db, "document", doc.id, "create", {"name": doc.name, "status": doc.status})
        created_working = False  # 提交后不因响应阶段异常删除已落库的内容。
        db.refresh(doc)
        return _build_document_response(doc)
    except Exception as exc:
        db.rollback()
        if created_working:
            try:
                shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
            except OSError:
                logger.exception("收录回滚失败，待清理工作副本: %s", target)
        if isinstance(exc, HTTPException):
            raise
        logger.exception("收录失败: %s", source)
        raise HTTPException(500, "收录失败，原文件保持不变，请检查存储后重试") from exc


@router.post("", response_model=DocumentResponse)
def create_document(req: DocumentCreate, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db)):
    return _ingest_document(req, db)


@router.post("/quick-add", response_model=DocumentResponse)
def quick_add_to_pending(req: DocumentQuickAdd, background_tasks: BackgroundTasks,
                         db: Session = Depends(get_db)):
    # 标签在整理时补充；快速收录与普通收录具有相同的版本保护。
    request = DocumentCreate(name=Path(req.source_path).name, source_path=req.source_path,
                             target_dir=settings.pending_folder_name, storage_mode="move", tags=["待整理"])
    return _ingest_document(request, db, pending=True)


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
    if doc.storage_mode == "copy":
        original_path = doc.original_path
        _purge_documents(db, [doc])
        return {"message": "已撤销收录，原文件保持不变", "original_path": original_path}
    if doc.storage_mode != "move":
        raise HTTPException(400, "该收录方式不支持撤销")
    if not doc.original_path:
        raise HTTPException(400, "无法获取原始路径，无法撤销")

    source = _owned_working_file(doc, db)
    target = Path(doc.original_path)
    if source is None:
        raise HTTPException(409, "工作文件缺失或被其他资料使用，无法安全撤销；保留资料与版本记录")
    with _working_changes(db) as move:
        move(source, target)
        record_change(db, "document", doc.id, "delete", {"name": doc.name})
        db.delete(doc)
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
        .join(Document, Document.current_version_id == Version.id)
        .filter(Document.id == document_id)
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
def search_documents(req: SearchRequest, request: Request, db: Session = Depends(get_db)):
    """搜索文档"""
    if req.include_content:
        from backend.app.api.ai import _local_request
        _local_request(request)
    query = db.query(Document)

    if req.favorites_only:
        from backend.app.models import Favorite
        query = query.filter(Document.status == 'organized', Document.id.in_(
            db.query(Favorite.local_document_id).filter(Favorite.target_type == 'local')))

    # 状态筛选：如果明确指定了状态则按状态过滤，否则默认排除回收站
    if req.status:
        query = query.filter(Document.status == req.status)
    else:
        query = query.filter(Document.status != "trashed")

    # 关键词搜索
    ranked_search = bool(req.include_content and req.keyword and req.keyword.strip())
    if req.keyword and not ranked_search:
        kw = f"%{req.keyword}%"
        conditions = [Document.name.ilike(kw), Document.description.ilike(kw)]
        if req.include_content:
            from backend.app.services.indexing import content_document_ids
            conditions.append(Document.id.in_(content_document_ids(req.keyword)))
        query = query.filter(or_(*conditions))

    # 标签筛选
    if req.tags:
        for tag_name in req.tags:
            query = query.filter(Document.tags.any(Tag.name == tag_name))

    # 类型筛选
    if req.is_folder is not None:
        query = query.filter(Document.is_folder == req.is_folder)

    # 目录过滤
    if req.folder is not None:
        query = query.filter(Document.status == 'organized')
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
                    Document.storage_path.startswith(prefix_slash, autoescape=True),
                    Document.storage_path.startswith(prefix_back, autoescape=True),
                )
            )

    if ranked_search:
        from backend.app.services.search_retrieval import search
        try:
            result = search(db, req.keyword, query.with_entities(Document.id).statement,
                            limit=req.page_size, offset=(req.page - 1) * req.page_size,
                            sort_by=req.sort_by, sort_order=req.sort_order)
        except ValueError:
            raise HTTPException(422, '检索条件无法处理，请使用不超过 16 个关键词并去除控制字符。') from None
        matches = {item['document_id']: item for item in result['items']}
        rows = db.query(Document).filter(Document.id.in_(matches)).all() if matches else []
        responses = {item['id']: item for item in build_document_responses(rows, db)}
        ordered = []
        for item in result['items']:
            response = responses[item['document_id']]
            response['search_match'] = SearchMatch(**{'mode': result['mode'], 'relevance': item['relevance'],
                'channels': item['channels'], 'passages': item['passages'],
                'coverage': item.get('coverage', {})})
            ordered.append(response)
        return DocumentListResponse(documents=ordered, total=result['total'],
                                    page=req.page, page_size=req.page_size)

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
    docs = query.offset(offset).limit(req.page_size).all()

    return DocumentListResponse(
        documents=build_document_responses(docs, db),
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
    docs = db.query(Document).filter(Document.id.in_(req.document_ids)).all()
    if not docs:
        raise HTTPException(404, "未找到指定文档")
    count = len(docs)
    _purge_documents(db, docs)
    return {"message": f"已永久删除 {count} 个文档"}


@router.post("/batch/restore")
def batch_restore_documents(req: BatchRestore, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.id.in_(req.document_ids), Document.status == "trashed").all()
    if not docs:
        raise HTTPException(404, "未找到可恢复的文档")
    with _working_changes(db) as move:
        for doc in docs:
            _restore_working(doc, move)
            record_change(db, "document", doc.id, "restore", {"name": doc.name})
    return {"message": f"已恢复 {len(docs)} 个文档"}


@router.post("/batch/organize")
def batch_organize_documents(req: BatchOrganize, db: Session = Depends(get_db)):
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")
    docs = db.query(Document).filter(Document.id.in_(req.document_ids)).all()
    if not docs or len(docs) != len(set(req.document_ids)) or any(d.status != "pending" for d in docs):
        raise HTTPException(400, "请仅选择存在且处于待整理状态的资料")
    with _working_changes(db) as move:
        tags = _get_or_create_tags(db, req.tags)
        if not tags:
            raise HTTPException(400, "请至少添加一个有效标签后完成整理")
        for doc in docs:
            _organize_working(doc, req.target_dir, None, [*doc.tags, *tags], move)
            record_change(db, "document", doc.id, "update", {"status": "organized"})
    return {"message": f"已整理 {len(docs)} 个文档"}


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
        folder_path = document_path(doc)
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
    doc = get_document_or_404(db, document_id)
    if doc.status != "pending":
        raise HTTPException(400, "该文档不在待整理状态")
    with _working_changes(db) as move:
        tags = _get_or_create_tags(db, req.tags)
        _organize_working(doc, req.target_dir, req.name, tags, move)
        record_change(db, "document", doc.id, "update", {"status": "organized"})
    return _build_document_response(doc)


@router.post("/{document_id}/move-to-pending", response_model=DocumentResponse)
def move_to_pending(document_id: str, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    if doc.status != "organized" or not doc.storage_path:
        raise HTTPException(400, "只有具有工作副本的已整理资料才能移回待整理区")
    source = document_path(doc)
    target = _working_location(str(Path(settings.pending_folder_name) / source.name), "pending")
    counter = 1
    while target.exists() and target != source:
        target = target.parent / f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    with _working_changes(db) as move:
        move(source, target)
        doc.storage_path = relative_location(target, "pending")
        doc.storage_area = "pending"
        doc.status = "pending"
    return _build_document_response(doc)


@router.delete("/{document_id}")
def delete_document(document_id: str, permanent: bool = False, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    if permanent:
        _purge_documents(db, [doc])
        return {"message": "已删除"}
    if doc.status == "trashed":
        return {"message": "已在回收站"}
    from backend.app.models import utc_now
    with _working_changes(db) as move:
        source = _owned_working_file(doc, db)
        if source:
            move(source, Path(settings.library_path) / ".teamhub" / "trash" / doc.id)
        doc.status = "trashed"
        doc.trashed_at = utc_now()
        record_change(db, "document", doc.id, "trash", {"name": doc.name})
    return {"message": "已移入回收站"}


@router.post("/{document_id}/remove-from-folder", response_model=DocumentResponse)
def remove_from_folder(document_id: str, db: Session = Depends(get_db)):
    """解除分类归属：移到库根目录，保留整理状态、标签、收藏和版本。"""
    return _move_classified_document(document_id, '', db)


@router.post("/{document_id}/move-to-folder", response_model=DocumentResponse)
def move_to_folder(document_id: str, req: DocumentFolderMove, db: Session = Depends(get_db)):
    """已整理资料归入分类目录，不生成新资料或版本。"""
    from backend.app.core.storage_paths import classification_directory
    classification_directory(req.target_dir, db)
    return _move_classified_document(document_id, req.target_dir, db)


def _move_classified_document(document_id, target_dir, db):
    doc = get_document_or_404(db, document_id)
    if doc.status != 'organized' or doc.storage_area != 'library':
        raise HTTPException(400, '只能调整已整理资料的文件夹归属')
    source = _owned_working_file(doc, db)
    if source is None:
        raise HTTPException(409, '没有可安全移动的工作副本，请检查文件位置')
    target = _working_location(str(Path(target_dir) / source.name))
    if doc.is_folder and target != source and target.is_relative_to(source):
        raise HTTPException(400, '不能将整体文件夹资料放入自身内部')
    pending = pending_root()
    if pending.is_relative_to(Path(settings.library_path).resolve()) and target.is_relative_to(pending):
        raise HTTPException(400, '移出文件夹不能将已整理资料放入待整理区域')
    # 根目录目标可能是另一个整体资料的祖先，不能覆盖或破坏其内部内容。
    for other in db.query(Document).filter(Document.id != doc.id, Document.status != 'trashed').all():
        if other.storage_path and other.is_folder:
            other_path = document_path(other)
            if source.is_relative_to(other_path) or target.is_relative_to(other_path):
                raise HTTPException(409, '不能移动另一份整体文件夹资料的内部内容')
    with _working_changes(db) as move:
        move(source, target)
        doc.storage_path = str(target.relative_to(Path(settings.library_path).resolve()))
        record_change(db, 'document', doc.id, 'update', {'storage_path': doc.storage_path})
    return _build_document_response(doc)


@router.post("/{document_id}/restore")
def restore_document(document_id: str, db: Session = Depends(get_db)):
    doc = get_document_or_404(db, document_id)
    if doc.status != "trashed":
        raise HTTPException(400, "文档不在回收站中")
    with _working_changes(db) as move:
        _restore_working(doc, move)
        record_change(db, "document", doc.id, "restore", {"name": doc.name})
    return {"message": "已恢复"}


@router.post("/{document_id}/relocate", response_model=DocumentResponse)
def relocate_document(document_id: str, req: DocumentRelocate, db: Session = Depends(get_db)):
    """重新定位缺失文档的文件路径"""
    doc = get_document_or_404(db, document_id)
    if doc.status != "missing":
        raise HTTPException(400, "只有缺失状态的文档才能重新定位")

    new_path = Path(req.new_path).resolve()
    if not new_path.exists():
        raise HTTPException(400, f"新路径不存在: {req.new_path}")

    library = Path(settings.library_path) if settings.library_path else None

    # 更新路径
    if doc.storage_area == "pending" and new_path.is_relative_to(pending_root()):
        doc.storage_path = relative_location(new_path, "pending")
    elif library and new_path.is_relative_to(library.resolve()):
        relative = str(new_path.relative_to(library.resolve()))
        _working_location(relative)
        doc.storage_path = relative
        doc.storage_area = "library"
    else:
        doc.storage_path = None
        doc.storage_mode = "index"
    doc.original_path = str(new_path)
    doc.status = "pending" if doc.storage_area == "pending" else "organized"


    db.commit()
    db.refresh(doc)
    logger.info(f"文档 {doc.id} 已重新定位到: {new_path}")
    return _build_document_response(doc)
