"""云会话配置写入的进程内互斥锁；网络请求不持有此锁。"""
import json
from pathlib import Path
from threading import RLock

session_lock = RLock()
# 续期网络操作和个人库存储切换共享同一入口；始终先于 session_lock 获取。
refresh_lock = RLock()


def read_config_at(path: Path) -> dict:
    """只读捕获的原配置路径，避免切换个人库后转读另一库。"""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError('个人库配置格式无效')
    return value
