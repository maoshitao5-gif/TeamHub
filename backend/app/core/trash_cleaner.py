"""
回收站自动清理模块
定期清理超过指定天数的已删除文档
"""
import shutil
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

        library = Path(library_path)
        trash_dir = library / '.teamhub' / 'trash'

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
                            logger.info(f"已删除回收站文件: {item.name}")

                # 删除数据库记录
                db.delete(doc)
                cleaned += 1
            except Exception as e:
                logger.error(f"清理文档 {doc.id} 失败: {e}")

        db.commit()
        logger.info(f"回收站自动清理完成，共清理 {cleaned} 个文档")
    except Exception as e:
        logger.error(f"回收站自动清理失败: {e}")
        db.rollback()
    finally:
        db.close()

    return cleaned
