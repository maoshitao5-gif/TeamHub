"""
文件服务模块
处理文件相关的业务逻辑
"""
import os
import shutil
import time
import random
from pathlib import Path
from typing import List, Optional
from fastapi import UploadFile, HTTPException
from sqlalchemy.orm import Session

from backend.app.models import File, Tag
from backend.app.config import STORAGE_DIR
from backend.app.core.encoding import safe_str, safe_print
from backend.app.core.file_utils import calculate_sha256, calculate_folder_content_hash
from backend.app.services.tag_service import get_or_create_tag


def save_uploaded_file(file: UploadFile, temp_file_path: Path) -> int:
    """
    保存上传的文件到临时位置
    
    Returns:
        文件大小（字节）
    """
    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    file_size = temp_file_path.stat().st_size
    if file_size == 0:
        os.remove(temp_file_path)
        raise HTTPException(
            status_code=400,
            detail="Empty file is not allowed. File size must be greater than 0 bytes."
        )
    
    return file_size


def calculate_file_hash(file_path: Path, is_folder_archive: Optional[str] = None) -> str:
    """
    计算文件哈希值
    
    Args:
        file_path: 文件路径
        is_folder_archive: 是否为文件夹压缩包
        
    Returns:
        SHA-256 哈希值
    """
    if is_folder_archive == 'true':
        safe_print(f"[查重] 检测到文件夹压缩包，使用内容哈希进行查重...")
        try:
            sha256_hash = calculate_folder_content_hash(str(file_path))
            safe_print(f"[查重] 文件夹内容哈希值计算完成: {sha256_hash}")
            return sha256_hash
        except Exception as e:
            safe_print(f"[查重] 计算内容哈希失败: {safe_str(e)}")
            safe_print(f"[查重] 回退到 ZIP 文件哈希...")
            return calculate_sha256(str(file_path))
    else:
        return calculate_sha256(str(file_path))


def create_file_record(
    db: Session,
    original_filename: str,
    storage_path: str,
    sha256_hash: str,
    file_size: int,
    tags: List[str],
    relative_path: Optional[str] = None
) -> File:
    """
    创建文件记录并关联标签
    
    Returns:
        File 对象
    """
    # 创建文件记录
    db_file = File(
        original_filename=original_filename,
        storage_path=storage_path,
        sha256_hash=sha256_hash,
        file_size=file_size,
        relative_path=relative_path
    )
    db.add(db_file)
    
    # 关联标签
    for tag_name in tags:
        if tag_name.strip():
            tag = get_or_create_tag(db, tag_name)
            db_file.tags.append(tag)
    
    db.commit()
    db.refresh(db_file)
    
    return db_file
