"""
版本自动清理模块(M2 起停用)

旧实现按 max_versions / max_version_age_days 自动删除版本文件与记录,
在不可变快照语义下可能删除仍被引用的历史内容(蓝图附录 B)。
M2 改为只记日志的 no-op;版本 GC 待后续里程碑以引用计数方式重新实现。
"""
from backend.app.core.logger import get_logger

logger = get_logger("version_cleaner")


def cleanup_old_versions(max_versions: int, max_age_days: int, library_path: str) -> int:
    """
    自动版本清理已停用(M2)。

    保留函数签名以兼容调用方;不删除任何版本记录或快照对象,
    仅在有限制条件配置时记录一条日志。
    """
    if max_versions > 0 or max_age_days > 0:
        logger.info(
            "版本自动清理已停用(M2 不可变快照语义,GC 待引用计数实现);"
            f"请求参数 max_versions={max_versions}, max_age_days={max_age_days}, 未执行删除"
        )
    return 0
