"""
TeamHub 云服务入口
独立 FastAPI 应用，端口 9000
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.api.router import register_routers
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime_security()
    print("TeamHub 云服务启动中...")
    init_db()
    # MVP 只使用受管本机存储；旧 OSS 路由已停用，不在启动时访问外部存储。
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
    # 网页会话严格同源；跨源桌面 API 使用 Bearer，不传浏览器 Cookie。
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routers(app)


@app.middleware("http")
async def secure_web_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
    return response


WEB_DIST = (Path(settings.cloud_web_dist).expanduser().resolve() if settings.cloud_web_dist
            else Path(__file__).resolve().parents[1] / "frontend" / "dist")
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="web-assets")


def _web_page(name: str):
    page = WEB_DIST / name
    if not page.is_file():
        raise HTTPException(503, "云端网页尚未构建，请先执行 npm run frontend:build")
    return FileResponse(page, media_type="text/html")


@app.get("/", include_in_schema=False)
@app.get("/cloud.html", include_in_schema=False)
def member_web():
    return _web_page("cloud.html")


@app.get("/admin.html", include_in_schema=False)
def admin_web():
    return _web_page("admin.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "teamhub-cloud"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
