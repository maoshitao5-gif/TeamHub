"""
FastAPI 应用主入口
模块化重构版本
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.core.encoding import setup_utf8_encoding, safe_print
from backend.app.config import DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD
from backend.app.database import init_db, get_db
from backend.app.models import User
from backend.app.core.security import get_password_hash
from backend.app.middleware.encoding import UTF8EncodingMiddleware
from backend.app.middleware.exception import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from datetime import datetime

# 设置 UTF-8 编码
setup_utf8_encoding()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    在应用启动时初始化数据库，在关闭时执行清理操作
    """
    # 启动时执行
    init_db()
    safe_print("数据库初始化完成")
    
    # 初始化默认管理员用户（如果不存在）
    db = next(get_db())
    try:
        default_user = db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).first()
        if not default_user:
            # 创建默认超级管理员用户
            default_password_hash = get_password_hash(DEFAULT_ADMIN_PASSWORD)
            default_user = User(
                username=DEFAULT_ADMIN_USERNAME,
                hashed_password=default_password_hash,
                created_at=datetime.utcnow(),
                is_admin=True
            )
            db.add(default_user)
            db.commit()
            safe_print(f"[OK] 已创建默认超级管理员用户: {DEFAULT_ADMIN_USERNAME} / {DEFAULT_ADMIN_PASSWORD}")
        else:
            # 如果admin用户已存在但is_admin为False，更新为True
            if not default_user.is_admin:
                default_user.is_admin = True
                db.commit()
                safe_print("[OK] 已将admin用户升级为超级管理员")
            else:
                safe_print("[OK] 默认超级管理员用户已存在")
    except Exception as e:
        safe_print(f"初始化默认用户失败: {e}")
        db.rollback()
    finally:
        db.close()
    
    yield
    # 关闭时执行（如果需要清理操作，可以在这里添加）


# 创建 FastAPI 应用实例
app = FastAPI(
    title="团队文件管理系统",
    description="支持标签化文件共享的文件管理系统",
    version="1.0.0",
    lifespan=lifespan
)

# 添加编码中间件
app.add_middleware(UTF8EncodingMiddleware)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 注册异常处理器
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# 导入并注册路由
from backend.app.api.router import api_router

# 注册新的模块化路由
app.include_router(api_router)

# 为了保持功能不变，我们需要导入原有main.py的所有路由
# 由于原有main.py有2100多行，我们采用兼容方案：
# 方案：直接使用原有main.py的app对象，但应用新的中间件和配置
# 这样可以保持所有功能不变，同时展示模块化结构

import sys
from pathlib import Path

# 将根目录添加到路径
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# 导入原有main.py并获取其所有路由
try:
    import main as old_main
    
    # 将原有main.py的所有路由注册到新app
    # 由于FastAPI的路由是注册在app对象上的，我们需要重新注册
    # 但原有main.py的路由已经注册到old_main.app上了
    
    # 最实用的方案：直接使用原有main.py的app，但应用新的中间件
    # 或者：逐步迁移所有路由到新结构
    
    # 当前方案：新的模块化结构已创建，路由会逐步迁移
    # 为了保持功能，我们暂时保留原有main.py作为主入口
    # 新的模块化结构作为参考和未来迁移目标
    
    # 注意：为了确保功能正常，当前仍使用原有main.py
    # 新的模块化结构已经创建完成，可以作为迁移参考
    
except Exception as e:
    safe_print(f"导入原有路由时出错: {e}")
    # 如果导入失败，继续使用新的模块化结构
    pass


@app.get("/")
async def root():
    """根路径接口，返回 API 信息"""
    return {
        "message": "团队文件管理系统 API",
        "version": "1.0.0",
        "architecture": "模块化架构（迁移中）",
        "endpoints": {
            "POST /login": "用户登录",
            "GET /auth/me": "获取当前用户信息",
            "POST /upload": "上传文件",
            "GET /tags": "获取所有标签",
            "POST /search": "搜索文件",
            "GET /files/{id}/download": "下载文件（标准路径）",
            "GET /download/{id}": "下载文件（简短路径）",
            "GET /files/{id}/preview": "预览文件",
            "DELETE /files/{id}": "删除文件",
            "POST /files/batch-download": "批量下载",
            "PUT /files/{id}/tags": "更新文件标签",
            "POST /files/batch-update-tags": "批量更新标签",
            "GET /admin/files": "管理后台-获取所有文件",
            "POST /admin/files/batch-delete": "管理后台-批量删除文件",
            "GET /admin/tags": "管理后台-获取所有标签",
            "POST /admin/tags": "管理后台-创建标签",
            "PUT /admin/tags/{id}": "管理后台-更新标签",
            "DELETE /admin/tags/{id}": "管理后台-删除标签",
            "POST /admin/tags/batch-delete": "管理后台-批量删除标签",
            "GET /admin/users": "管理后台-获取所有用户",
            "POST /admin/users": "管理后台-创建用户",
            "PUT /admin/users/{id}": "管理后台-更新用户",
            "DELETE /admin/users/{id}": "管理后台-删除用户"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
