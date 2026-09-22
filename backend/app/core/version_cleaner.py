"""
版本自动清理模块
按 max_versions 和 max_version_age_days 策略删除过期版本
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backend.app.core.logger import get_logger

logger = get_logger("version_cleaner")


def cleanup_old_versions(max_versions: int, max_age_days: int, library_path: str) -> int:
    """
    清理超出限制的版本

    Args:
        max_versions: 每个文档最多保留的版本数，0 表示不限制
        max_age_days: 版本最长保留天数，0 表示不限制
        library_path: 文件库路径
    Returns:
        删除的版本记录数
    """
    from backend.app.database import SessionLocal
    from backend.app.models import Document, Version

    db = SessionLocal()
    deleted = 0

    try:
        if max_versions <= 0 and max_age_days <= 0:
            logger.info("版本清理：未设置限制条件，跳过")
            return 0

        # 按版本数限制清理
        if max_versions > 0:
            docs = db.query(Document).filter(
                Document.status.in_(["organized", "pending", "missing"])
            ).all()
            for doc in docs:
                versions = db.query(Version).filter(
                    Version.document_id == doc.id
                ).order_by(Version.version_number.desc()).all()

                if len(versions) <= max_versions:
                    continue

                to_delete = versions[max_versions:]
                for v in to_delete:
                    if v.is_current:
                        continue  # 不删除当前版本
                    try:
                        file_path = Path(v.file_path)
                        if file_path.exists() and '.versions' in str(file_path):
                            file_path.unlink()
                            logger.debug(f"删除版本文件: {file_path}")
                    except OSError as e:
                        logger.warning(f"删除版本文件失败: {e}")
                    db.delete(v)
                    deleted += 1

        # 按时间限制清理
        if max_age_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            old_versions = db.query(Version).filter(
                Version.created_at < cutoff,
                Version.is_current == False  # noqa: E712
            ).all()
            for v in old_versions:
                try:
                    file_path = Path(v.file_path)
                    if file_path.exists() and '.versions' in str(file_path):
                        file_path.unlink()
                        logger.debug(f"删除过期版本文件: {file_path}")
                except OSError as e:
                    logger.warning(f"删除过期版本文件失败: {e}")
                db.delete(v)
                deleted += 1

        db.commit()
        logger.info(f"版本清理完成，共删除 {deleted} 个版本")
    except Exception as e:
        logger.error(f"版本清理失败: {e}")
        db.rollback()
    finally:
        db.close()

    return deleted
