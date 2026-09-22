"""
变更日志记录工具
在各 CRUD 端点中调用，与主操作共享同一事务，失败时仅警告不回滚
"""
import json
from sqlalchemy.orm import Session
from backend.app.models import ChangeLog
from backend.app.core.device import get_device_id
from backend.app.core.logger import get_logger

logger = get_logger("changelog")


def record_change(
    db: Session,
    entity_type: str,   # "document" / "version" / "tag"
    entity_id: str,
    operation: str,     # "create" / "update" / "delete" / "trash" / "restore" / "tag_add" / "tag_remove"
    payload: dict | None = None
):
    """
    记录一条变更日志
    调用时机：在 db.commit() 之前，与主操作共享同一个事务
    """
    if not entity_id:
        logger.warning(f"变更日志跳过：entity_id 为空 (entity_type={entity_type}, operation={operation})")
        return
    try:
        entry = ChangeLog(
            device_id=get_device_id(),
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            payload=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
        )
        db.add(entry)
    except Exception as e:
        logger.warning(f"变更日志记录失败（不影响主操作）: {e}")
