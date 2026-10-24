"""
应用配置模块
使用 pydantic-settings 管理配置，支持环境变量
支持 Electron 桌面应用环境
"""
import os
import re
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# 沙箱模式(由 scripts/mvp-dev.sh 通过 env -i 设置 TEAMHUB_SANDBOX=1):
# 禁用 .env 文件读取,配合脚本的干净环境,防止上级目录 .env 或继承环境变量
# 把 library_path / database_url 等带出沙箱(M0-R01)。
SANDBOX = os.getenv("TEAMHUB_SANDBOX", "").lower() in ("1", "true", "yes")


def _desktop_development_origin() -> str | None:
    """仅接受 Electron 已验证启动器提供的单一本机开发入口。"""
    value = os.getenv("TEAMHUB_DEV_FRONTEND_URL", "")
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/?", value)
    if match is None:
        return None
    port = int(match.group(1))
    if port > 65535 or port == 80:
        return None
    return f"http://127.0.0.1:{port}"


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
            origins.append("null")  # 打包 file:// 页面的 Origin；浏览器开发模式不开放。
            if development_origin := _desktop_development_origin():
                if development_origin not in origins:
                    origins.append(development_origin)
            for port in range(8001, 8011):
                origins.extend([
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                ])
        return origins

    # 文件夹快照与解包保护上限；环境变量可调整，试用样本校准留 M7。
    shared_download_max_bytes: int = Field(3 * 1024**3, ge=1)
    snapshot_max_entries: int = Field(10000, ge=1)
    snapshot_max_file_bytes: int = Field(1024 * 1024 * 1024, ge=1)
    snapshot_max_total_bytes: int = Field(2 * 1024 * 1024 * 1024, ge=1)

    # 文件大小限制（单文件）
    max_file_size: int = 1024 * 1024 * 1024  # 1GB

    # 默认收纳方式: move / copy / index
    default_storage_mode: str = "move"

    # 回收站自动清理天数
    trash_auto_clean_days: int = 30

    # 同名文件冲突处理策略：rename（自动重命名）/ version（追加为新版本）
    on_conflict: str = "rename"

    # 文档库默认排序
    default_sort_by: str = "updated_at"
    default_sort_order: str = "desc"

    # 启动时自动扫描文件库
    auto_scan_on_startup: bool = False

    # 每页显示数量（文档库和待整理页面）
    items_per_page: int = 15

    # 悬浮窗开关（默认开启）
    enable_floating_window: bool = True

    # 文档库展示模式
    library_show_flat_view: bool = True   # 平铺展示（全部文档列表）
    library_show_tree_view: bool = True   # 目录树展示（左侧导航面板）

    # 待整理区文件夹名称（可自定义，默认"待整理"）
    pending_folder_name: str = "待整理"
    pending_path: Optional[str] = None

    # 云后端服务地址（运行时可配置，生产环境指向阿里云部署）
    cloud_api_url: str = "http://localhost:9000"

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
        env_file=None if SANDBOX else [".env", "../.env", "../../.env"],
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
