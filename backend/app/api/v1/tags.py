"""
标签相关路由
包含标签查询、统计等功能
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Tag
from backend.app.api.deps import get_current_user

router = APIRouter(tags=["标签管理"])


# 注意：标签路由暂时保留在main.py中
# 后续会逐步迁移到本模块
