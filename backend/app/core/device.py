"""
设备标识管理模块
生成并持久化当前设备的唯一 ID，内存缓存避免重复读文件
"""
import socket
from pathlib import Path
from backend.app.config import settings
from backend.app.core.cloud_session import session_lock, read_config_at
from backend.app.models import generate_uuid
from backend.app.core.logger import get_logger

logger = get_logger("device")

# 内存缓存
_cached_device_id: str | None = None


def get_device_id() -> str:
    """获取当前设备 ID，首次调用时生成并写入 config.json"""
    global _cached_device_id
    # 延迟导入避免循环依赖
    from backend.app.database import write_json_config
    with session_lock:
        if _cached_device_id:
            return _cached_device_id
        config_path = Path(settings.config_file)
        config = read_config_at(config_path)
        if config.get("device_id"):
            _cached_device_id = config["device_id"]
            return _cached_device_id

        new_id = generate_uuid()
        config["device_id"] = new_id
        config.setdefault("device_name", socket.gethostname())
        write_json_config(config_path, config)
        _cached_device_id = new_id
        logger.info(f"新设备 ID 已生成: {new_id}")
        return _cached_device_id


def reset_device_id_cache():
    """切换文件库路径时调用，清除内存缓存"""
    global _cached_device_id
    with session_lock:
        _cached_device_id = None
