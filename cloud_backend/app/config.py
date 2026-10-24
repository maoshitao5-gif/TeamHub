"""
云服务配置模块
使用 pydantic-settings 读取 .env 文件
"""
import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 沙箱模式(由 scripts/mvp-dev.sh 设置 TEAMHUB_SANDBOX=1):禁用 .env 读取,
# 防止上级/默认 .env 把 OSS 等配置带出沙箱(M0-R01)。
SANDBOX = os.getenv("TEAMHUB_SANDBOX", "").lower() in ("1", "true", "yes")


class CloudSettings(BaseSettings):
    # 数据库
    database_url: str = "sqlite:///./cloud_dev.db"

    # JWT
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30
    login_account_attempts: int = Field(10, ge=1)
    login_ip_attempts: int = Field(60, ge=1)
    login_window_seconds: int = Field(60, ge=1)
    login_bucket_limit: int = Field(10000, ge=2)

    # CORS（逗号分隔字符串，自动解析为列表）
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 服务端口
    port: int = 9000
    # 独立网页构建目录；空值使用仓库 frontend/dist，可部署时显式指定。
    cloud_web_dist: str = ""

    # 受管文件存储目录(M4,蓝图 §3.1):服务端独立文件目录,不依赖 OSS/MinIO;
    # 沙箱由 mvp-dev.sh 显式 STORAGE_DIR 指向沙箱,测试用 tmp_path 覆盖
    storage_dir: str = "./cloud_storage"
    upload_max_bytes: int = Field(3 * 1024**3, ge=1)
    snapshot_max_entries: int = Field(10000, ge=1)
    snapshot_max_file_bytes: int = Field(1024**3, ge=1)
    snapshot_max_total_bytes: int = Field(2 * 1024**3, ge=1)
    # 暂存单独限制物理容量；只回收过期票据，不删除已发布对象。
    staging_max_bytes: int = Field(6 * 1024**3, ge=1)
    staging_max_uploads: int = Field(1000, ge=1)
    staging_ttl_seconds: int = Field(24 * 60 * 60, ge=1)

    model_config = SettingsConfigDict(
        env_file=None if SANDBOX else [".env", ".env.production", ".env.dev"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def validate_runtime_security(self) -> None:
        """启动服务前拒绝缺失或公开的签名密钥；不在错误中回显配置。"""
        secret = self.jwt_secret_key.strip()
        if (len(secret.encode("utf-8")) < 32 or secret in {
            "dev-secret-key-change-in-production-min32chars",
            "replace-with-a-random-secret-at-least-32-characters",
        }):
            raise RuntimeError("JWT_SECRET_KEY 必须设置为独立生成的至少 32 字节随机密钥")


settings = CloudSettings()
