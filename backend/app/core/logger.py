"""
日志模块
统一的日志配置和管理
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from backend.app.config import settings


def setup_logging():
    """
    设置应用日志系统

    配置：
    - 控制台输出：INFO 级别以上
    - 文件输出：所有级别，带日志轮转
    - 格式：时间 | 级别 | 模块名 | 消息
    """
    # 创建日志目录
    log_file = Path(settings.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # 创建 logger
    logger = logging.getLogger("teamhub")
    logger.setLevel(getattr(logging, settings.log_level.upper()))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler（带日志轮转：10MB per file, 保留 5 个备份）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# 创建全局 logger 实例
logger = setup_logging()


def get_logger(name: str = None) -> logging.Logger:
    """
    获取 logger 实例

    Args:
        name: logger 名称，如果为 None 则返回根 logger

    Returns:
        Logger 实例
    """
    if name:
        return logging.getLogger(f"teamhub.{name}")
    return logging.getLogger("teamhub")
