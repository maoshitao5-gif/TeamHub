"""
文件相关路由
包含文件上传、下载、删除等功能
"""
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form, Path as PathParam, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from pathlib import Path
from typing import List, Optional
import os
import shutil
import time
import random
import zipfile
import io

from backend.app.database import get_db
from backend.app.models import File as FileModel, Tag
from backend.app.api.schemas import (
    BatchDownloadRequest, UpdateFileTagsRequest, BatchUpdateTagsRequest
)
from backend.app.api.deps import get_current_user
from backend.app.config import STORAGE_DIR
from backend.app.core.encoding import safe_str, safe_print
from backend.app.core.file_utils import calculate_sha256, calculate_folder_content_hash
from backend.app.services.tag_service import get_or_create_tag

router = APIRouter(tags=["文件管理"])


# 注意：由于代码量很大，文件上传等路由暂时保留在main.py中
# 后续会逐步迁移到本模块
# 当前本模块作为占位符，展示模块化结构
