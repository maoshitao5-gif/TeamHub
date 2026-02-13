"""
API 路由注册模块
统一注册所有API路由
"""
from fastapi import APIRouter

from backend.app.api.v1 import auth, storage

# 创建主路由器
api_router = APIRouter()

# 注册各个模块的路由
# 注意：为了保持功能不变，我们暂时保留原有main.py的路由
# 逐步将路由迁移到各个模块中

# 注册认证路由（已迁移）
api_router.include_router(auth.router)

# 注册存储位置管理路由
api_router.include_router(storage.router)

# 其他路由暂时保留在main.py中，逐步迁移
# TODO: 迁移文件路由到 app.api.v1.files
# TODO: 迁移标签路由到 app.api.v1.tags  
# TODO: 迁移搜索路由到 app.api.v1.search
# TODO: 迁移管理路由到 app.api.v1.admin
