"""受管资料删除：提交前隔离工作内容，数据库失败时恢复原位置。"""
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.core.changelog import record_change
from backend.app.core.file_utils import find_trash_entries
from backend.app.core.logger import get_logger
from backend.app.core.storage_paths import document_path
from backend.app.models import Document
from backend.app.services.preorganize_storage import working_changes

logger = get_logger("services.document_storage")


def owned_working_file(doc: Document, db: Session) -> Path | None:
    """仅返回该资料独占的工作副本，避免改动另一份整体资料的内部内容。"""
    if (not settings.library_path or doc.status == "trashed"
            or doc.storage_mode == "index" or not doc.storage_path):
        return None
    library = Path(settings.library_path).resolve()
    path = document_path(doc)
    if path.is_relative_to(library / ".teamhub"):
        raise HTTPException(400, "拒绝删除受管工作目录之外的路径")
    others = db.query(Document).filter(Document.id != doc.id, Document.status != "trashed").all()
    for other in others:
        other_path = (document_path(other) if other.storage_path
                      else Path(other.original_path) if other.original_path else None)
        if other_path:
            other_path = other_path.resolve()
            if (other_path == path or other_path.is_relative_to(path)
                    or path.is_relative_to(other_path)):
                raise HTTPException(409, "工作位置与其他资料重叠，无法安全移动或删除；请先核对资料归属")
    return path if path.exists() else None


def purge_documents(db: Session, docs: list[Document]) -> None:
    """提交后清除隔离内容；提交失败补偿原工作位置，快照始终保留。"""
    if not settings.library_path:
        raise HTTPException(400, "文件库未初始化")
    trash = Path(settings.library_path) / ".teamhub" / "trash"
    staging = trash / f"purge-{uuid.uuid4().hex}"
    with working_changes(db) as move:
        for doc in docs:
            paths = (find_trash_entries(trash, doc.id, doc.storage_path) if doc.status == "trashed"
                     else [path] if (path := owned_working_file(doc, db)) else [])
            for path in paths:
                move(path, staging / doc.id)
            record_change(db, "document", doc.id, "delete", {"name": doc.name})
            db.delete(doc)
    if staging.exists():
        try:
            shutil.rmtree(staging)
        except OSError:
            logger.exception("资料已删除，隔离区内容待清理: %s", staging)
