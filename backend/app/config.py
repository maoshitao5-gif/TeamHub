"""
应用配置模块
使用 pydantic-settings 管理配置，支持环境变量
支持 Electron 桌面应用环境
"""
import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    # Electron 环境检测
    is_electron: bool = os.getenv('ELECTRON_APP', 'false').lower() == 'true'
    user_data_dir: str = os.getenv('USER_DATA_DIR', '.')

    # 文件库路径（用户选择的文件库根目录）
    # 首次启动时为空，用户通过设置页面选择后写入 config.json
    library_path: Optional[str] = None

    # CORS 配置
    _base_allowed_origins: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]

    @property
    def allowed_origins(self) -> List[str]:
        """获取允许的 CORS 源列表"""
        origins = self._base_allowed_origins.copy()
        if self.is_electron:
            for port in range(8001, 8011):
                origins.extend([
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                ])
        return origins

    # 文件大小限制（单文件）
    max_file_size: int = 1024 * 1024 * 1024  # 1GB

    # 默认收纳方式: move / copy / index
    default_storage_mode: str = "move"

    # 版本保留策略
    max_versions: int = 0  # 0 = 不限制
    max_version_age_days: int = 0  # 0 = 不限制

    # 回收站自动清理天数
    trash_auto_clean_days: int = 30

    # 日志配置
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        """获取数据库 URL — 数据库存放在文件库的 .teamhub/ 目录下"""
        if self.library_path:
            db_path = Path(self.library_path) / '.teamhub' / 'db.sqlite'
            return f"sqlite:///{db_path.resolve()}"
        # 如果还没有设置文件库路径，使用 Electron user data 目录或当前目录
        if self.is_electron:
            db_path = Path(self.user_data_dir) / 'database' / 'teamhub.db'
            return f"sqlite:///{db_path.resolve()}"
        return "sqlite:///./teamhub.db"

    @property
    def log_file(self) -> str:
        """获取日志文件路径"""
        if self.is_electron:
            return str(Path(self.user_data_dir) / 'logs' / 'teamhub.log')
        return "logs/teamhub.log"

    @property
    def config_file(self) -> str:
        """获取配置文件路径（用于持久化 library_path 等设置）"""
        if self.library_path:
            return str(Path(self.library_path) / '.teamhub' / 'config.json')
        if self.is_electron:
            return str(Path(self.user_data_dir) / 'config' / 'config.json')
        return "config.json"

    model_config = SettingsConfigDict(
        env_file=[".env", "../.env", "../../.env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


# 创建全局配置实例
try:
    settings = Settings()

    if settings.is_electron:
        print(f"[Config] Running in Electron mode")
        print(f"[Config] User data directory: {settings.user_data_dir}")
        print(f"[Config] Database URL: {settings.database_url}")
        print(f"[Config] Log file: {settings.log_file}")
    else:
        print(f"[Config] Running in standard mode")

except Exception as e:
    import sys
    print(f"配置错误: {e}")
    sys.exit(1)

# 向后兼容
ALLOWED_ORIGINS = settings.allowed_origins
