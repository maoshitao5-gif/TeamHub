"""
应用配置模块
使用 pydantic-settings 管理配置，支持环境变量
"""
import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    # JWT 配置
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30 * 24 * 60  # 30天过期

    # 文件存储配置
    storage_dir: str = "storage"

    # 数据库配置
    database_url: str = "sqlite:///./teamhub.db"

    # CORS 配置
    allowed_origins: List[str] = [
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

    # 默认管理员配置
    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"

    # 文件上传限制
    max_file_size: int = 1024 * 1024 * 1024  # 1GB
    allowed_file_extensions: List[str] = []  # 空列表表示允许所有类型

    # 日志配置
    log_level: str = "INFO"
    log_file: str = "logs/teamhub.log"

    model_config = SettingsConfigDict(
        # 支持从多个位置读取 .env 文件（从当前目录向上查找）
        env_file=[".env", "../.env", "../../.env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


# 创建全局配置实例
try:
    settings = Settings()
except Exception as e:
    # 如果缺少必需的环境变量，提供友好的错误提示
    import sys
    print(f"配置错误: {e}")
    print("\n请创建 .env 文件并设置以下必需的环境变量：")
    print("  SECRET_KEY=your-secret-key-here")
    print("\n可选的环境变量：")
    print("  DATABASE_URL=sqlite:///./teamhub.db")
    print("  STORAGE_DIR=storage")
    print("  LOG_LEVEL=INFO")
    sys.exit(1)

# 为了向后兼容，保留旧的常量名称
SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
STORAGE_DIR = Path(settings.storage_dir)
STORAGE_DIR.mkdir(exist_ok=True, parents=True)
ALLOWED_ORIGINS = settings.allowed_origins
DEFAULT_ADMIN_USERNAME = settings.default_admin_username
DEFAULT_ADMIN_PASSWORD = settings.default_admin_password
