"""
云服务配置模块
使用 pydantic-settings 读取 .env 文件
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudSettings(BaseSettings):
    # 数据库
    database_url: str = "sqlite:///./cloud_dev.db"

    # JWT
    jwt_secret_key: str = "dev-secret-key-change-in-production-min32chars"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # OSS
    oss_endpoint: str = "http://localhost:9001"
    oss_access_key_id: str = "minioadmin"
    oss_access_key_secret: str = "minioadmin"
    oss_bucket_name: str = "teamhub-dev"
    oss_prefix: str = "teamhub-cloud"

    # CORS（逗号分隔字符串，自动解析为列表）
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 服务端口
    port: int = 9000

    model_config = SettingsConfigDict(
        env_file=[".env", ".env.production", ".env.dev"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = CloudSettings()
