"""
搜索相关路由
包含文件搜索功能
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from backend.app.database import get_db
from backend.app.models import File, Tag
from backend.app.api.schemas import SearchRequest
from backend.app.api.deps import get_current_user

router = APIRouter(tags=["搜索"])


# 注意：搜索路由暂时保留在main.py中
# 后续会逐步迁移到本模块
