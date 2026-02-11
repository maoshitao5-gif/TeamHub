"""
应用配置模块
"""
import os
from pathlib import Path
from typing import List

# JWT 配置
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production-please-use-env-variable")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30 * 24 * 60  # 30天过期

# 文件存储配置
STORAGE_DIR = Path("storage")
STORAGE_DIR.mkdir(exist_ok=True)

# CORS 配置
ALLOWED_ORIGINS: List[str] = [
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
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
