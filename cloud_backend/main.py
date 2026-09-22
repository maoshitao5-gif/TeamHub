"""
TeamHub 云服务入口
独立 FastAPI 应用，端口 9000
"""
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.api.router import register_routers
from app.core.oss import ensure_bucket_exists


def _init_oss_background():
    """在后台线程初始化 OSS Bucket，避免阻塞服务器启动（boto3 重试可能耗时数十秒）"""
    try:
        ensure_bucket_exists()
        print("OSS Bucket 初始化完成")
    except Exception as e:
        print(f"警告：OSS Bucket 初始化失败（离线模式）: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("TeamHub 云服务启动中...")
    init_db()
    # OSS 初始化放入后台线程，避免 boto3 重试阻塞启动（系统代理可能导致 30s 延迟）
    threading.Thread(target=_init_oss_background, daemon=True, name="oss-init").start()
    print(f"TeamHub 云服务已启动，监听端口 {settings.port}")
    yield
    print("TeamHub 云服务关闭")


app = FastAPI(
    title="TeamHub 云服务",
    description="多租户文档管理云服务，提供认证、团队、工作空间和增量同步 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list + ["app://.", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routers(app)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "teamhub-cloud"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
