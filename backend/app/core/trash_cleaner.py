"""
回收站自动清理模块
定期清理超过指定天数的已删除文档
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.app.core.logger import get_logger

logger = get_logger("trash_cleaner")


def cleanup_old_trash(days: int, library_path: str):
    """
    清理超过指定天数的回收站文档

    Args:
        days: 超过多少天的文档将被清理
        library_path: 文件库路径
    Returns:
        清理的文档数量
    """
    from backend.app.database import SessionLocal
    from backend.app.models import Document
    from backend.app.services.document_storage import purge_documents

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    cleaned = 0

    try:
        # 查询超期的回收站文档
        docs = db.query(Document).filter(
            Document.status == "trashed",
            Document.trashed_at < cutoff
        ).all()

        if not docs:
            logger.info("回收站无需清理")
            return 0

        # 会话固定当前库，拒绝拿另一个库的路径清理本库资料。
        expected_db = Path(library_path) / ".teamhub" / "db.sqlite"
        if Path(db.bind.url.database).resolve() != expected_db.resolve():
            raise ValueError("回收站清理路径与当前文件库不一致")
        count = len(docs)
        purge_documents(db, docs)
        cleaned = count
        logger.info(f"回收站自动清理完成，共清理 {cleaned} 个文档")
    except Exception as e:
        logger.error(f"回收站自动清理失败: {e}")
        db.rollback()
    finally:
        db.close()

    return cleaned
