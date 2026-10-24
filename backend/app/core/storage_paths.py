"""资料归属决定工作位置；待整理路径可独立于版本/数据库所在的文件库。"""
from pathlib import Path
from fastapi import HTTPException
from backend.app.config import settings


def pending_root():
    return Path(settings.pending_path or (Path(settings.library_path) / settings.pending_folder_name)).resolve()


def working_location(relative, area='library'):
    parts = Path(relative)
    if parts.is_absolute() or '..' in parts.parts or '\\' in str(relative):
        raise HTTPException(400, '非法资料相对路径')
    root = Path(settings.library_path).resolve()
    if area == 'pending':
        root = pending_root()
        # 兼容已登记资料的逻辑路径；前缀不再表示实际物理父目录。
        prefix = settings.pending_folder_name
        try:
            parts = parts.relative_to(prefix)
        except ValueError:
            raise HTTPException(400, '待整理资料路径与归属不一致')
    raw = root / parts
    if any(p.is_symlink() for p in [raw, *raw.parents] if p.is_relative_to(root)):
        raise HTTPException(400, '资料路径不能包含符号链接')
    target = raw.resolve()
    metadata = Path(settings.library_path).resolve() / '.teamhub'
    if target == root or not target.is_relative_to(root) or target.is_relative_to(metadata):
        raise HTTPException(400, '工作文件必须位于所属区域内，且不能位于系统目录')
    return target


def document_path(doc):
    if doc.storage_path:
        return working_location(doc.storage_path, doc.storage_area)
    if doc.storage_mode == 'index' and doc.original_path:
        return Path(doc.original_path)
    raise HTTPException(409, '资料未登记工作位置')


def classification_folder_roots(db) -> tuple[Path, ...]:
    """请求内预取整体资料边界；不加载标签、历史等与目录校验无关的数据。"""
    from backend.app.models import Document
    rows = db.query(Document.storage_path, Document.storage_area,
                    Document.storage_mode, Document.original_path).filter(
        Document.is_folder.is_(True), Document.storage_path.isnot(None),
        Document.status != 'trashed',
    ).all()
    return tuple(document_path(row) for row in rows)


def classification_directory(relative, db, allow_root=False, *, folder_roots=None):
    """分类目录不能位于暂存区、系统目录或已登记整体资料内部。"""
    if not settings.library_path:
        raise HTTPException(400, '文件库未初始化')
    root = Path(settings.library_path).resolve()
    target = root if allow_root and relative == '' else working_location(relative)
    pending = pending_root()
    if pending.is_relative_to(root) and target.is_relative_to(pending):
        raise HTTPException(400, '分类目录不能位于待整理区域')
    if folder_roots is None:
        folder_roots = classification_folder_roots(db)
    for folder in folder_roots:
        if target.is_relative_to(folder):
            raise HTTPException(409, '整体文件夹资料不能作为分类目录')
    return target


def relative_location(path, area):
    root = pending_root() if area == 'pending' else Path(settings.library_path).resolve()
    relative = Path(path).resolve().relative_to(root)
    return str(Path(settings.pending_folder_name) / relative) if area == 'pending' else str(relative)
