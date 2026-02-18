"""
文档 API 路由
包含文档的收纳、搜索、更新、删除等操作
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from backend.app.database import get_db
from backend.app.models import Document, Version, Tag, document_tag
from backend.app.config import settings
from backend.app.core.file_utils import calculate_sha256
from backend.app.core.logger import get_logger
from backend.app.api.schemas import (
    DocumentCreate, DocumentUpdate, DocumentOrganize, DocumentQuickAdd,
    BatchOrganize, BatchTagUpdate, BatchDelete, BatchRestore,
    MergeAsVersions, SearchRequest, DocumentRelocate,
    DocumentResponse, DocumentListResponse, PendingCountResponse,
    TagResponse, VersionResponse,
    PendingAnalysisResponse, SimilarGroup, SimilarGroupDocument, TagSuggestion
)

logger = get_logger("api.documents")
router = APIRouter(prefix="/api/documents", tags=["documents"])


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
        "current_version": None
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

    source = Path(req.source_path)
    if not source.exists():
        raise HTTPException(400, f"源路径不存在: {req.source_path}")

    library = Path(settings.library_path)
    is_folder = source.is_dir() if req.is_folder else False

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

    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.post("/quick-add", response_model=DocumentResponse)
def quick_add_to_pending(
    req: DocumentQuickAdd,
    db: Session = Depends(get_db)
):
    """快速放入待整理区（不需要打标签和分类）"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    source = Path(req.source_path)
    if not source.exists():
        raise HTTPException(400, f"源路径不存在: {req.source_path}")

    library = Path(settings.library_path)
    pending_dir = library / "待整理"
    pending_dir.mkdir(exist_ok=True)

    is_folder = source.is_dir()
    target = pending_dir / source.name

    # 避免重名
    if target.exists():
        stem = source.stem
        suffix = source.suffix
        counter = 1
        while target.exists():
            target = pending_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    # 移动到待整理区
    shutil.move(str(source), str(target))

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
    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


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


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, db: Session = Depends(get_db)):
    """获取文档详情"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")
    return _build_document_response(doc)


@router.get("/{document_id}/files")
def get_document_files(document_id: str, db: Session = Depends(get_db)):
    """获取文件夹文档的内容树"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")
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
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    if req.name is not None:
        doc.name = req.name
    if req.description is not None:
        doc.description = req.description

    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.put("/{document_id}/tags")
def update_document_tags(document_id: str, tag_names: list[str], db: Session = Depends(get_db)):
    """设置文档的标签（替换）"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    doc.tags = _get_or_create_tags(db, tag_names)
    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.post("/{document_id}/organize", response_model=DocumentResponse)
def organize_document(document_id: str, req: DocumentOrganize, db: Session = Depends(get_db)):
    """整理文档：从待整理区移入文件库指定位置"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")
    if doc.status != "pending":
        raise HTTPException(400, "该文档不在待整理状态")

    library = Path(settings.library_path)
    target_dir = library / req.target_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    # 当前文件位置
    current_path = library / doc.storage_path if doc.storage_path else None
    if current_path and current_path.exists():
        new_name = req.name or doc.name
        target_path = target_dir / new_name

        # 如果是文件，保留原扩展名
        if not doc.is_folder and not target_path.suffix:
            target_path = target_path.with_suffix(current_path.suffix)

        if target_path.exists():
            raise HTTPException(400, f"目标位置已存在: {target_path.name}")

        shutil.move(str(current_path), str(target_path))
        doc.storage_path = str(target_path.relative_to(library))

        # 更新版本的文件路径
        for v in doc.versions:
            if v.is_current and not v.relative_path:
                v.file_path = str(target_path)

    if req.name:
        doc.name = req.name
    doc.status = "organized"
    if req.tags:
        doc.tags = _get_or_create_tags(db, req.tags)

    db.commit()
    db.refresh(doc)
    return _build_document_response(doc)


@router.delete("/{document_id}")
def delete_document(document_id: str, permanent: bool = False, db: Session = Depends(get_db)):
    """删除文档（默认移入回收站，permanent=true 永久删除）"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")

    if permanent or doc.status == "trashed":
        # 永久删除：物理删除文件和数据库记录
        if doc.storage_path and settings.library_path:
            file_path = Path(settings.library_path) / doc.storage_path
            if file_path.exists():
                if file_path.is_dir():
                    shutil.rmtree(str(file_path))
                else:
                    file_path.unlink()
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
    db.commit()
    return {"message": "已恢复"}


@router.post("/{document_id}/relocate", response_model=DocumentResponse)
def relocate_document(document_id: str, req: DocumentRelocate, db: Session = Depends(get_db)):
    """重新定位缺失文档的文件路径"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "文档不存在")
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
        # 删除物理文件
        if doc.storage_path and settings.library_path:
            file_path = Path(settings.library_path) / doc.storage_path
            if file_path.exists():
                if file_path.is_dir():
                    shutil.rmtree(str(file_path))
                else:
                    file_path.unlink()
        # 如果在回收站中，也尝试删除回收站里的文件
        if doc.status == "trashed" and settings.library_path:
            trash_dir = Path(settings.library_path) / '.teamhub' / 'trash'
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
        restored_count += 1

    db.commit()
    return {"message": f"已恢复 {restored_count} 个文档"}


@router.post("/merge-as-versions")
def merge_as_versions(
    req: MergeAsVersions,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """合并多个文档为一个文档的多个版本"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    # 获取所有指定文档
    docs = db.query(Document).filter(Document.id.in_(req.document_ids)).all()
    if len(docs) < 2:
        raise HTTPException(400, "至少需要 2 个文档才能合并")

    # 校验：必须都是单文件且状态为 pending
    for doc in docs:
        if doc.is_folder:
            raise HTTPException(400, f"文档 \"{doc.name}\" 是文件夹，不支持合并为版本")
        if doc.status not in ("pending", "organized"):
            raise HTTPException(400, f"文档 \"{doc.name}\" 状态不支持合并")

    # 按创建时间排序，最新的作为 base 文档
    docs.sort(key=lambda d: d.created_at)
    base_doc = docs[-1]
    older_docs = docs[:-1]

    library = Path(settings.library_path)

    # 更新 base 文档信息
    base_doc.name = req.name
    if req.description is not None:
        base_doc.description = req.description

    # 确保 base 文档有 .versions 目录
    if base_doc.storage_path:
        base_path = library / base_doc.storage_path
        versions_dir = base_path.parent / '.versions'
        versions_dir.mkdir(exist_ok=True)
    else:
        versions_dir = None

    # 确保 base 文档有版本记录
    existing_versions = db.query(Version).filter(
        Version.document_id == base_doc.id,
        Version.relative_path.is_(None)
    ).order_by(Version.version_number.desc()).all()

    if not existing_versions:
        # quick_add 创建的文档没有 Version 记录，补建
        if base_doc.storage_path:
            bp = library / base_doc.storage_path
            v = Version(
                document_id=base_doc.id,
                version_number=1,
                file_path=str(bp),
                file_size=base_doc.total_size,
                original_filename=bp.name,
                is_current=True
            )
            db.add(v)
            db.flush()
            background_tasks.add_task(_compute_hash_background, v.id, str(bp))
            next_version_number = 2
        else:
            next_version_number = 1
    else:
        next_version_number = existing_versions[0].version_number + 1

    # 将旧文档的文件移入 base 文档的 .versions/ 目录，创建版本记录
    # 旧文档按时间从旧到新排列，版本号从小到大
    # 重新分配：旧文档获得较小的版本号
    # 先把 base 的当前版本号调整到最大
    final_version_number = len(older_docs) + (1 if existing_versions else 1)

    # 简单方案：旧的文档从 v1 开始编号，base 文档的当前版本号为最大
    version_num = 1
    # 先将 base 的现有版本号重新编排
    if existing_versions:
        for ev in reversed(existing_versions):
            # 保留但不重新编号（已有版本保持不动）
            pass
        version_num = existing_versions[0].version_number + 1

    # 处理旧文档（按时间从旧到新），分配递增版本号
    for old_doc in older_docs:
        if old_doc.storage_path:
            old_path = library / old_doc.storage_path
            if old_path.exists() and versions_dir:
                # 移动到 .versions/ 目录
                dest_name = f"{old_path.stem}_v{version_num}{old_path.suffix}"
                dest_path = versions_dir / dest_name
                shutil.move(str(old_path), str(dest_path))
                file_path_str = str(dest_path)
            else:
                file_path_str = str(old_path) if old_path.exists() else ""
        else:
            file_path_str = ""

        if file_path_str:
            file_size = os.path.getsize(file_path_str) if os.path.isfile(file_path_str) else 0
            v = Version(
                document_id=base_doc.id,
                version_number=version_num,
                file_path=file_path_str,
                file_size=file_size,
                original_filename=old_doc.name,
                is_current=False
            )
            db.add(v)
            db.flush()
            background_tasks.add_task(_compute_hash_background, v.id, file_path_str)

        version_num += 1

    # 更新 base 文档的当前版本号为最大
    if existing_versions:
        current_ver = existing_versions[0]
        if version_num > current_ver.version_number:
            current_ver.version_number = version_num
            current_ver.is_current = True

    # 删除旧文档的 DB 记录
    for old_doc in older_docs:
        db.delete(old_doc)

    db.commit()
    db.refresh(base_doc)
    logger.info(f"合并完成：{len(older_docs)} 个文档合并到 \"{base_doc.name}\"")
    return _build_document_response(base_doc)


@router.post("/batch/organize")
def batch_organize_documents(req: BatchOrganize, db: Session = Depends(get_db)):
    """批量整理文档"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")

    docs = db.query(Document).filter(
        Document.id.in_(req.document_ids),
        Document.status == "pending"
    ).all()
    if not docs:
        raise HTTPException(404, "未找到待整理文档")

    library = Path(settings.library_path)
    target_dir = library / req.target_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    tags = _get_or_create_tags(db, req.tags) if req.tags else []
    organized_count = 0

    for doc in docs:
        current_path = library / doc.storage_path if doc.storage_path else None
        if current_path and current_path.exists():
            target_path = target_dir / current_path.name

            if target_path.exists():
                logger.warning(f"目标位置已存在: {target_path}，跳过文档 {doc.id}")
                continue

            shutil.move(str(current_path), str(target_path))
            doc.storage_path = str(target_path.relative_to(library))

            # 更新版本的文件路径
            for v in doc.versions:
                if v.is_current and not v.relative_path:
                    v.file_path = str(target_path)

        doc.status = "organized"
        if tags:
            doc.tags = tags
        organized_count += 1

    db.commit()
    return {"message": f"已整理 {organized_count} 个文档"}
