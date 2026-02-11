"""
管理后台路由
包含文件管理、标签管理、用户管理等功能
需要管理员权限
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pathlib import Path
import os

from backend.app.database import get_db
from backend.app.models import File, Tag, User, file_tag_association
from backend.app.api.schemas import (
    CreateUserRequest, UpdateUserRequest, CreateTagRequest, UpdateTagRequest,
    BatchDeleteRequest, ResetPasswordRequest, BatchCreateUserRequest
)
from backend.app.api.deps import get_admin_user
from backend.app.core.security import get_password_hash
from backend.app.core.encoding import safe_str, safe_print
from backend.app.services.tag_service import get_or_create_tag

router = APIRouter(tags=["管理后台"])


# 注意：管理后台路由暂时保留在main.py中
# 后续会逐步迁移到本模块
