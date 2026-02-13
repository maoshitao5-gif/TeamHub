"""
FastAPI 应用主入口
提供文件上传、标签管理和文件搜索等核心功能
模块化重构版本
"""
import os
import sys
import shutil
import urllib.parse
import time
import random
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

# ========== 全局 UTF-8 编码配置 ==========
# 使用模块化的编码配置
from backend.app.core.encoding import setup_utf8_encoding, safe_str, safe_print
setup_utf8_encoding()
# ========== 全局 UTF-8 编码配置结束 ==========

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, Path as PathParam, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
import zipfile
import io
import traceback

# 使用模块化的数据库和模型
from backend.app.database import get_db, init_db
from backend.app.models import File, Tag, file_tag_association

# 使用模块化的配置
from backend.app.config import STORAGE_DIR
from backend.app.core.file_utils import calculate_sha256, calculate_folder_content_hash
from backend.app.services.tag_service import get_or_create_tag

# 使用模块化的API模型
from backend.app.api.schemas import (
    SearchRequest, CreateTagRequest, UpdateTagRequest, BatchDeleteRequest, BatchDownloadRequest,
    UpdateFileTagsRequest, BatchUpdateTagsRequest
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    在应用启动时初始化数据库，在关闭时执行清理操作
    """
    # 启动时执行
    init_db()
    print("数据库初始化完成")
    
    yield
    # 关闭时执行（如果需要清理操作，可以在这里添加）


# 创建 FastAPI 应用实例
app = FastAPI(
    title="团队文件管理系统",
    description="支持标签化文件共享的文件管理系统",
    version="1.0.0",
    lifespan=lifespan
)

# 使用模块化的中间件和异常处理
from backend.app.middleware.encoding import UTF8EncodingMiddleware
from backend.app.middleware.exception import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler
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

# 注册模块化API路由
from backend.app.api.router import api_router
app.include_router(api_router)

# 文件存储目录已在config模块中定义
# STORAGE_DIR 已从 backend.app.config 导入
# 确保目录存在
STORAGE_DIR.mkdir(exist_ok=True)


def resolve_file_path(file_record: File, db: Session) -> Path:
    """
    解析文件路径的辅助函数
    支持多存储位置和旧数据兼容
    
    Args:
        file_record: 文件记录对象
        db: 数据库会话
        
    Returns:
        解析后的文件路径（Path对象）
    """
    storage_path_str = file_record.storage_path
    file_path = Path(storage_path_str)
    
    # 如果已经是绝对路径且存在，直接返回
    if file_path.is_absolute() and file_path.exists():
        return file_path
    
    # 优先使用关联的存储位置
    if file_record.storage_location_id:
        from backend.app.models import StorageLocation
        storage_location = db.query(StorageLocation).filter(
            StorageLocation.id == file_record.storage_location_id
        ).first()
        if storage_location:
            # 如果是相对路径，尝试在存储位置下查找
            if not file_path.is_absolute():
                file_path = Path(storage_location.path) / Path(storage_path_str).name
            else:
                file_path = Path(storage_path_str)
            if file_path.exists():
                return file_path
    
    # 兼容旧数据：使用默认存储位置
    if not file_path.is_absolute():
        file_path = STORAGE_DIR / Path(storage_path_str).name
    
    return file_path


# 以下工具函数已迁移到模块化结构中，保留作为向后兼容
# 实际使用时会优先使用模块化版本（已在文件开头导入）
def safe_str(obj) -> str:
    """
    安全地将对象转换为字符串，处理 Unicode 编码错误
    在 Windows GBK 环境下，某些 Unicode 字符无法编码，需要特殊处理
    """
    try:
        s = str(obj)
        # 首先尝试编码为 GBK（Windows 默认编码），如果失败则使用 ASCII 安全版本
        try:
            s.encode('gbk')
            return s
        except UnicodeEncodeError:
            # 如果 GBK 编码失败，使用 ASCII 安全版本（替换无法编码的字符）
            return s.encode('ascii', 'replace').decode('ascii')
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        # 如果转换失败，使用 ASCII 安全版本
        try:
            return str(obj).encode('ascii', 'replace').decode('ascii')
        except:
            return repr(obj)


def safe_print(*args, **kwargs):
    """
    安全的 print 函数，处理 Windows GBK 编码环境下的 Unicode 字符
    """
    try:
        # 将所有参数转换为安全的字符串
        safe_args = []
        for arg in args:
            if isinstance(arg, str):
                # 对于字符串，先尝试安全转换
                safe_arg = safe_str(arg)
                # 确保可以编码为GBK（Windows默认编码）
                try:
                    safe_arg.encode('gbk')
                    safe_args.append(safe_arg)
                except UnicodeEncodeError:
                    # 如果GBK编码失败，使用ASCII安全版本
                    safe_args.append(safe_arg.encode('ascii', 'replace').decode('ascii'))
            else:
                safe_args.append(arg)
        print(*safe_args, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        # 如果仍然失败，使用 repr 输出
        try:
            safe_args = [repr(arg) for arg in args]
            print(*safe_args, **kwargs)
        except:
            # 最后的备选方案：输出错误信息
            try:
                print(f"[编码错误] 无法输出内容: {type(e).__name__}")
            except:
                pass  # 如果连这个都失败，就静默失败


# 以下函数已迁移到模块化结构中，保留作为向后兼容
def calculate_sha256(file_path: str) -> str:
    """向后兼容函数，实际调用模块化版本"""
    from backend.app.core.file_utils import calculate_sha256 as _calculate_sha256
    return _calculate_sha256(file_path)


def calculate_folder_content_hash(zip_path: str) -> str:
    """向后兼容函数，实际调用模块化版本"""
    from backend.app.core.file_utils import calculate_folder_content_hash as _calculate_folder_content_hash
    return _calculate_folder_content_hash(zip_path)


# get_or_create_tag 已迁移到 backend.app.services.tag_service
# 已在文件开头导入，这里保留作为向后兼容
def get_or_create_tag(db: Session, tag_name: str) -> Tag:
    """向后兼容函数，实际调用模块化版本"""
    from backend.app.services.tag_service import get_or_create_tag as _get_or_create_tag
    return _get_or_create_tag(db, tag_name)


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(),
    tags: List[str] = Form(),
    is_folder_archive: Optional[str] = Form(None),  # 标识是否为文件夹压缩包
    folder_name: Optional[str] = Form(None),  # 文件夹名称
    storage_location_id: Optional[int] = Form(None),  # 存储位置ID
    db: Session = Depends(get_db)
):
    """
    文件上传接口
    
    功能：
    1. 接收文件和标签列表
    2. 计算文件的 SHA-256 哈希值进行查重
    3. 如果文件已存在则返回 400 错误
    4. 保存文件到 storage/ 目录
    5. 将文件元数据和标签保存到数据库
    
    Args:
        file: 上传的文件对象
        tags: 标签列表（字符串数组）
        is_folder_archive: 是否为文件夹压缩包（'true' 表示是）
        folder_name: 文件夹名称（当 is_folder_archive 为 'true' 时使用）
        storage_location_id: 存储位置ID（可选，不提供则使用默认存储位置）
        db: 数据库会话（依赖注入）
        
    Returns:
        上传成功的文件信息
        
    Raises:
        HTTPException: 如果文件已存在或上传失败
    """
    try:
        # 创建临时文件路径用于计算哈希值
        # 使用时间戳和随机数确保临时文件名唯一，避免并发冲突
        # 使用安全的文件名，避免特殊字符导致编码错误
        safe_filename = safe_str(file.filename)
        temp_filename = f"temp_{int(time.time())}_{random.randint(1000, 9999)}_{safe_filename}"
        temp_file_path = STORAGE_DIR / temp_filename
        
        safe_print(f"[上传] 开始接收文件: {file.filename}")
        safe_print(f"[上传] 临时文件路径: {temp_file_path}")
        
        # 保存上传的文件到临时位置
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 检查文件大小，禁止上传0字节的空文件
        file_size = temp_file_path.stat().st_size
        safe_print(f"[上传] 文件保存完成，大小: {file_size} bytes")
        
        if file_size == 0:
            # 删除临时文件
            os.remove(temp_file_path)
            raise HTTPException(
                status_code=400,
                detail="Empty file is not allowed. File size must be greater than 0 bytes."
            )
        
        # 计算文件的 SHA-256 哈希值
        print(f"[查重] ========== 开始查重流程 ==========")
        safe_print(f"[查重] 文件名: {file.filename}")
        print(f"[查重] 文件大小: {file_size} bytes")
        print(f"[查重] 是否为文件夹压缩包: {is_folder_archive}")
        safe_print(f"[查重] 文件夹名称: {folder_name}")
        
        # 如果是文件夹压缩包，使用内容哈希（基于文件夹内所有文件的内容）
        # 这样可以检测到相同内容但不同文件夹名称的重复
        if is_folder_archive == 'true':
            print(f"[查重] 检测到文件夹压缩包，使用内容哈希进行查重...")
            print(f"[查重] 内容哈希会忽略文件夹名称和ZIP结构，只关注文件内容")
            try:
                sha256_hash = calculate_folder_content_hash(str(temp_file_path))
                print(f"[查重] 文件夹内容哈希值计算完成: {sha256_hash}")
                print(f"[查重] 哈希值前16位: {sha256_hash[:16]}...")
                print(f"[查重] 哈希值后16位: ...{sha256_hash[-16:]}")
            except Exception as e:
                safe_print(f"[查重] 计算内容哈希失败: {safe_str(e)}")
                # 如果计算内容哈希失败，回退到 ZIP 文件哈希
                print(f"[查重] 回退到 ZIP 文件哈希...")
                sha256_hash = calculate_sha256(str(temp_file_path))
                print(f"[查重] ZIP 文件哈希值: {sha256_hash[:16]}...")
        else:
            # 普通文件，使用 ZIP 文件哈希值
            print(f"[查重] 普通文件，使用文件哈希进行查重...")
            sha256_hash = calculate_sha256(str(temp_file_path))
            print(f"[查重] 文件哈希值计算完成: {sha256_hash}")
            print(f"[查重] 哈希值前16位: {sha256_hash[:16]}...")
            print(f"[查重] 哈希值后16位: ...{sha256_hash[-16:]}")
        
        # 查重：检查数据库中是否已存在相同哈希值的文件
        print(f"[查重] 查询数据库中是否存在相同哈希值的文件...")
        print(f"[查重] 查询条件: sha256_hash = {sha256_hash}")
        
        # 先查询所有文件的哈希值，用于调试
        all_files = db.query(File).all()
        print(f"[查重] 数据库中总共有 {len(all_files)} 个文件")
        if len(all_files) > 0:
            print(f"[查重] 数据库中已有文件的哈希值:")
            for f in all_files[:10]:  # 只显示前10个
                safe_print(f"  - 文件ID {f.id}: {f.sha256_hash[:16]}... (文件名: {f.original_filename})")
        
        existing_file = db.query(File).filter(File.sha256_hash == sha256_hash).first()
        print(f"[查重] 查询结果: {'找到重复文件' if existing_file else '未找到重复文件'}")
        
        if existing_file:
            print(f"[查重] [WARNING] 检测到重复文件！")
            print(f"[查重] 已存在文件ID: {existing_file.id}")
            safe_print(f"[查重] 已存在文件名: {existing_file.original_filename}")
            print(f"[查重] 已存在文件大小: {existing_file.file_size} bytes")
            print(f"[查重] 已存在文件哈希: {existing_file.sha256_hash}")
            print(f"[查重] 已存在文件上传时间: {existing_file.upload_time}")
            
            # 删除临时文件
            try:
                os.remove(temp_file_path)
                print(f"[查重] 临时文件已删除")
            except Exception as e:
                safe_print(f"[查重] 删除临时文件失败: {safe_str(e)}")
            
            # 返回详细的文件信息，方便前端展示
            error_detail = {
                "error_type": "duplicate_file",
                "message": "系统已存在相同内容的文件，无需重复上传",
                "existing_file": {
                    "id": existing_file.id,
                    "original_filename": existing_file.original_filename,
                    "file_size": existing_file.file_size,
                    "sha256_hash": existing_file.sha256_hash,
                    "upload_time": existing_file.upload_time.isoformat() if existing_file.upload_time else None,
                    "tags": [tag.name for tag in existing_file.tags] if existing_file.tags else []
                }
            }
            print(f"[查重] 返回重复文件错误响应")
            print(f"[查重] ========== 查重流程结束（检测到重复） ==========")
            raise HTTPException(
                status_code=400,
                detail=error_detail
            )
        
        safe_print(f"[查重] [OK] 未检测到重复文件，继续上传流程...")
        print(f"[查重] ========== 查重流程结束（无重复） ==========")
        
        # 获取存储位置
        from backend.app.models import StorageLocation
        if storage_location_id:
            storage_location = db.query(StorageLocation).filter(
                StorageLocation.id == storage_location_id,
                StorageLocation.enabled == True
            ).first()
            if not storage_location:
                os.remove(temp_file_path)
                raise HTTPException(
                    status_code=400,
                    detail="指定的存储位置不存在或已禁用"
                )
        else:
            # 使用默认存储位置
            storage_location = db.query(StorageLocation).filter(
                StorageLocation.is_default == True,
                StorageLocation.enabled == True
            ).first()
            if not storage_location:
                os.remove(temp_file_path)
                raise HTTPException(
                    status_code=500,
                    detail="未找到可用的默认存储位置"
                )
        
        # 生成存储路径：使用哈希值的前8位 + 原始文件名，避免文件名冲突
        # 使用安全的文件名，避免特殊字符导致编码错误
        file_extension = Path(safe_filename).suffix
        storage_filename = f"{sha256_hash[:8]}_{safe_filename}"
        storage_dir = Path(storage_location.path)
        storage_path = storage_dir / storage_filename
        
        # 确保存储目录存在
        storage_dir.mkdir(parents=True, exist_ok=True)
        
        # 将临时文件移动到最终存储位置（使用绝对路径）
        final_storage_path = storage_path.resolve()
        shutil.move(str(temp_file_path), str(final_storage_path))
        
        # 文件大小已在之前检查时获取，直接使用
        
        # 创建文件记录
        # 如果是文件夹压缩包，将文件夹名称存储在 relative_path 字段中
        relative_path_value = None
        if is_folder_archive == 'true' and folder_name:
            relative_path_value = folder_name
        
        db_file = File(
            original_filename=file.filename,
            storage_path=str(final_storage_path),  # 存储绝对路径
            sha256_hash=sha256_hash,
            file_size=file_size,
            upload_time=datetime.utcnow(),
            relative_path=relative_path_value,
            storage_location_id=storage_location.id
        )
        db.add(db_file)
        db.flush()  # 刷新以获取文件ID
        
        # 处理标签：获取或创建标签，并建立关联关系
        for tag_name in tags:
            if tag_name.strip():  # 忽略空标签
                tag = get_or_create_tag(db, tag_name)
                db_file.tags.append(tag)
        
        db.commit()
        db.refresh(db_file)
        
        result = {
            "id": db_file.id,
            "original_filename": db_file.original_filename,
            "file_size": db_file.file_size,
            "sha256_hash": db_file.sha256_hash,
            "upload_time": db_file.upload_time.isoformat(),
            "tags": [tag.name for tag in db_file.tags]
        }
        
        # 如果是文件夹压缩包，添加额外信息
        if is_folder_archive == 'true':
            result["is_folder_archive"] = True
            result["folder_name"] = folder_name
        
        return result
        
    except HTTPException:
        # 重新抛出 HTTP 异常
        raise
    except Exception as e:
        # 清理临时文件（如果存在）
        if temp_file_path.exists():
            os.remove(temp_file_path)
        # 使用安全字符串转换，避免 GBK 编码错误
        error_msg = safe_str(e)
        raise HTTPException(status_code=500, detail=f"文件上传失败: {error_msg}")


@app.post("/upload-folder")
async def upload_folder(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    文件夹上传接口（批量上传）
    
    功能：
    1. 接收多个文件和它们的相对路径
    2. 计算每个文件的 SHA-256 哈希值进行查重
    3. 保存文件到 storage/ 目录，保持文件夹结构
    4. 将文件元数据和标签保存到数据库
    
    Args:
        request: FastAPI 请求对象（用于解析 multipart/form-data）
        db: 数据库会话（依赖注入）
        
    Returns:
        上传成功的文件信息列表
        
    Raises:
        HTTPException: 如果上传失败
    """
    try:
        # 解析 multipart/form-data
        form = await request.form()
        
        # 获取文件列表和相对路径列表
        files = form.getlist('files')
        relative_paths = form.getlist('relative_paths')
        tags = form.getlist('tags')
        storage_location_id_str = form.get('storage_location_id')
        storage_location_id = int(storage_location_id_str) if storage_location_id_str else None
        
        # 过滤出有效的文件对象
        files = [f for f in files if hasattr(f, 'file')]
        
        if len(files) != len(relative_paths):
            raise HTTPException(
                status_code=400,
                detail=f"文件数量({len(files)})与路径数量({len(relative_paths)})不匹配"
            )
        
        if not files:
            raise HTTPException(
                status_code=400,
                detail="文件列表不能为空"
            )
        
        # 获取存储位置
        from backend.app.models import StorageLocation
        if storage_location_id:
            storage_location = db.query(StorageLocation).filter(
                StorageLocation.id == storage_location_id,
                StorageLocation.enabled == True
            ).first()
            if not storage_location:
                raise HTTPException(
                    status_code=400,
                    detail="指定的存储位置不存在或已禁用"
                )
        else:
            # 使用默认存储位置
            storage_location = db.query(StorageLocation).filter(
                StorageLocation.is_default == True,
                StorageLocation.enabled == True
            ).first()
            if not storage_location:
                raise HTTPException(
                    status_code=500,
                    detail="未找到可用的默认存储位置"
                )
        
        storage_dir = Path(storage_location.path)
        storage_dir.mkdir(parents=True, exist_ok=True)
        
        uploaded_files = []
        failed_files = []
        
        for idx, (file, relative_path) in enumerate(zip(files, relative_paths)):
            try:
                # 创建临时文件路径用于计算哈希值
                temp_file_path = STORAGE_DIR / f"temp_{idx}_{file.filename}"
                
                # 保存上传的文件到临时位置
                with open(temp_file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                
                # 检查文件大小，禁止上传0字节的空文件
                file_size = temp_file_path.stat().st_size
                if file_size == 0:
                    os.remove(temp_file_path)
                    failed_files.append({
                        "filename": file.filename,
                        "relative_path": relative_path,
                        "error": "空文件不允许上传"
                    })
                    continue
                
                # 计算文件的 SHA-256 哈希值
                sha256_hash = calculate_sha256(str(temp_file_path))
                
                # 查重：检查数据库中是否已存在相同哈希值的文件
                existing_file = db.query(File).filter(File.sha256_hash == sha256_hash).first()
                if existing_file:
                    # 删除临时文件
                    os.remove(temp_file_path)
                    failed_files.append({
                        "filename": file.filename,
                        "relative_path": relative_path,
                        "error": "文件已存在",
                        "existing_file_id": existing_file.id
                    })
                    continue
                
                # 生成存储路径：使用哈希值的前8位 + 原始文件名，避免文件名冲突
                # 如果 relative_path 不为空，在存储目录下创建对应的文件夹结构
                if relative_path and relative_path.strip():
                    # 规范化相对路径（去除开头的 / 或 \）
                    normalized_path = relative_path.strip().lstrip('/\\')
                    # 获取文件的目录部分（去除文件名）
                    path_obj = Path(normalized_path)
                    if path_obj.parent and path_obj.parent != Path('.'):
                        # 创建文件夹结构（相对于存储位置目录）
                        folder_path = storage_dir / path_obj.parent
                        folder_path.mkdir(parents=True, exist_ok=True)
                        # 存储文件名使用哈希值前缀 + 原始文件名
                        storage_filename = f"{sha256_hash[:8]}_{path_obj.name}"
                        storage_path = folder_path / storage_filename
                    else:
                        # 文件在根目录，直接存储在存储位置根目录
                        storage_filename = f"{sha256_hash[:8]}_{path_obj.name}"
                        storage_path = storage_dir / storage_filename
                else:
                    # 单文件上传模式，直接存储在存储位置根目录
                    storage_filename = f"{sha256_hash[:8]}_{file.filename}"
                    storage_path = storage_dir / storage_filename
                
                # 将临时文件移动到最终存储位置（使用绝对路径）
                final_storage_path = storage_path.resolve()
                shutil.move(str(temp_file_path), str(final_storage_path))
                
                # 创建文件记录
                db_file = File(
                    original_filename=file.filename,
                    storage_path=str(final_storage_path),  # 存储绝对路径
                    sha256_hash=sha256_hash,
                    file_size=file_size,
                    upload_time=datetime.utcnow(),
                    relative_path=relative_path if relative_path and relative_path.strip() else None,
                    storage_location_id=storage_location.id
                )
                db.add(db_file)
                db.flush()  # 刷新以获取文件ID
                
                # 处理标签：获取或创建标签，并建立关联关系
                for tag_name in tags:
                    if tag_name.strip():  # 忽略空标签
                        tag = get_or_create_tag(db, tag_name)
                        db_file.tags.append(tag)
                
                uploaded_files.append({
                    "id": db_file.id,
                    "original_filename": db_file.original_filename,
                    "relative_path": db_file.relative_path,
                    "file_size": db_file.file_size,
                    "sha256_hash": db_file.sha256_hash,
                    "upload_time": db_file.upload_time.isoformat(),
                    "tags": [tag.name for tag in db_file.tags]
                })
                
            except Exception as e:
                # 记录失败的文件
                failed_files.append({
                    "filename": file.filename,
                    "relative_path": relative_path,
                    "error": str(e)
                })
                # 清理临时文件（如果存在）
                temp_file_path = STORAGE_DIR / f"temp_{idx}_{file.filename}"
                if temp_file_path.exists():
                    os.remove(temp_file_path)
        
        # 提交所有成功的文件
        db.commit()
        
        return {
            "success": True,
            "uploaded_count": len(uploaded_files),
            "failed_count": len(failed_files),
            "uploaded_files": uploaded_files,
            "failed_files": failed_files
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"文件夹上传失败: {str(e)}")


@app.get("/tags")
async def get_tags(
    db: Session = Depends(get_db)
):
    """
    获取所有标签接口（按使用频率排序）
    
    返回系统中所有已存在的标签列表，按使用频率（关联文件数量）降序排列
    最常用的标签排在前面，用于前端搜索建议和自动完成
    
    Args:
        db: 数据库会话（依赖注入）
        
    Returns:
        标签列表，包含标签ID、名称和使用频率
    """
    # 查询所有标签，并统计每个标签关联的文件数量
    tags = db.query(Tag).all()
    
    # 计算每个标签的使用频率（关联的文件数量）
    tag_list = []
    for tag in tags:
        file_count = len(tag.files) if tag.files else 0
        tag_list.append({
            "id": tag.id,
            "name": tag.name,
            "file_count": file_count
        })
    
    # 按使用频率降序排序，频率相同的按名称排序
    tag_list.sort(key=lambda x: (-x["file_count"], x["name"]))
    
    return {
        "tags": tag_list,
        "total": len(tag_list)
    }


@app.get("/tags/stats")
async def get_tags_stats(
    db: Session = Depends(get_db)
):
    """
    获取标签统计信息接口
    
    返回标签的详细统计信息，包括每个标签关联的文件数量
    用于标签管理页面
    
    Args:
        db: 数据库会话（依赖注入）
        
    Returns:
        标签统计信息列表，包含标签ID、名称、关联文件数量和文件列表
    """
    tags = db.query(Tag).all()
    
    tag_stats = []
    for tag in tags:
        file_count = len(tag.files) if tag.files else 0
        files = [
            {
                "id": file.id,
                "original_filename": file.original_filename,
                "upload_time": file.upload_time.isoformat()
            }
            for file in tag.files
        ] if tag.files else []
        
        tag_stats.append({
            "id": tag.id,
            "name": tag.name,
            "file_count": file_count,
            "files": files
        })
    
    # 按文件数量降序排序
    tag_stats.sort(key=lambda x: -x["file_count"])
    
    return {
        "tags": tag_stats,
        "total": len(tag_stats),
        "total_files": sum(stat["file_count"] for stat in tag_stats)
    }


# ==================== 标签管理 API ====================

@app.post("/tags")
async def create_tag(
    request: CreateTagRequest,
    db: Session = Depends(get_db)
):
    """
    创建标签
    """
    tag_name = request.name.strip().lower()
    if not tag_name:
        raise HTTPException(status_code=400, detail="标签名称不能为空")
    
    # 检查标签是否已存在
    existing_tag = db.query(Tag).filter(Tag.name == tag_name).first()
    if existing_tag:
        raise HTTPException(status_code=400, detail="标签已存在")
    
    tag = Tag(name=tag_name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    
    return {
        "id": tag.id,
        "name": tag.name,
        "message": "标签创建成功"
    }


@app.put("/tags/{tag_id}")
async def update_tag(
    tag_id: int,
    request: UpdateTagRequest,
    db: Session = Depends(get_db)
):
    """
    更新标签名称
    """
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    
    new_name = request.name.strip().lower()
    if not new_name:
        raise HTTPException(status_code=400, detail="标签名称不能为空")
    
    # 检查新名称是否已被其他标签使用
    existing_tag = db.query(Tag).filter(Tag.name == new_name, Tag.id != tag_id).first()
    if existing_tag:
        raise HTTPException(status_code=400, detail="标签名称已被使用")
    
    tag.name = new_name
    db.commit()
    db.refresh(tag)
    
    return {
        "id": tag.id,
        "name": tag.name,
        "message": "标签更新成功"
    }


@app.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: int,
    db: Session = Depends(get_db)
):
    """
    删除标签（会解除所有文件的关联）
    """
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    
    # 获取关联的文件数量
    file_count = len(tag.files) if tag.files else 0
    
    # 删除标签（会自动解除与文件的关联）
    db.delete(tag)
    db.commit()
    
    return {
        "message": f"标签删除成功，已解除 {file_count} 个文件的关联",
        "deleted_tag_id": tag_id,
        "unlinked_files_count": file_count
    }


@app.post("/tags/batch-delete")
async def batch_delete_tags(
    request: BatchDeleteRequest,
    db: Session = Depends(get_db)
):
    """
    批量删除标签
    """
    if not request.ids:
        raise HTTPException(status_code=400, detail="标签ID列表不能为空")
    
    tags = db.query(Tag).filter(Tag.id.in_(request.ids)).all()
    if not tags:
        raise HTTPException(status_code=404, detail="未找到标签")
    
    deleted_count = 0
    total_unlinked = 0
    
    for tag in tags:
        file_count = len(tag.files) if tag.files else 0
        total_unlinked += file_count
        db.delete(tag)
        deleted_count += 1
    
    db.commit()
    
    return {
        "message": f"成功删除 {deleted_count} 个标签，已解除 {total_unlinked} 个文件的关联",
        "deleted_count": deleted_count,
        "unlinked_files_count": total_unlinked
    }


@app.post("/search")
async def search_files(
    search_request: SearchRequest,
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    文件搜索接口
    
    根据关键词或标签列表搜索匹配的文件
    
    搜索逻辑：
    1. 如果提供了关键词，在文件名中搜索（不区分大小写）
    2. 如果提供了标签，查找包含这些标签的文件
    3. 如果同时提供了关键词和标签，返回同时满足条件的文件
    
    Args:
        search_request: 搜索请求对象，包含 keywords 和 tags 字段
        db: 数据库会话（依赖注入）
        
    Returns:
        匹配的文件列表
    """
    # 从请求对象中提取参数
    keywords = search_request.keywords
    tags = search_request.tags
    
    # 构建查询
    query = db.query(File)
    
    # 如果提供了关键词，在文件名中搜索
    if keywords:
        keyword_conditions = []
        for keyword in keywords:
            if keyword.strip():
                keyword_conditions.append(
                    File.original_filename.ilike(f"%{keyword.strip()}%")
                )
        if keyword_conditions:
            query = query.filter(or_(*keyword_conditions))
    
    # 如果提供了标签，查找包含这些标签的文件
    if tags:
        tag_conditions = []
        for tag_name in tags:
            if tag_name.strip():
                tag_name_normalized = tag_name.strip().lower()
                tag_obj = db.query(Tag).filter(Tag.name == tag_name_normalized).first()
                if tag_obj:
                    tag_conditions.append(File.tags.contains(tag_obj))
        
        if tag_conditions:
            # 使用 and_ 确保文件包含所有指定的标签（交集）
            # 如果希望包含任一标签即可（并集），可以使用 or_
            query = query.filter(and_(*tag_conditions))
    
    # 执行查询并按上传时间倒序排列
    files = query.order_by(File.upload_time.desc()).all()
    
    return {
        "files": [
            {
                "id": file.id,
                "original_filename": file.original_filename,
                "file_size": file.file_size,
                "sha256_hash": file.sha256_hash,
                "upload_time": file.upload_time.isoformat(),
                "tags": [tag.name for tag in file.tags]
            }
            for file in files
        ],
        "total": len(files)
    }


def _download_file_helper(file_id: int, db: Session, request: Request = None):
    """
    下载文件的辅助函数
    """
    try:
        # 1. 基础查询逻辑保持不变
        file_record = db.query(File).filter(File.id == file_id).first()
        if not file_record:
            raise HTTPException(status_code=404, detail="文件不存在")
        
        storage_path_str = file_record.storage_path
        file_path = Path(storage_path_str)
        
        # 路径解析增强（针对不同系统的兼容）
        if not file_path.is_absolute():
            if not file_path.exists():
                filename_only = Path(storage_path_str).name
                file_path = STORAGE_DIR / filename_only

        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
        
        # 2. 核心修正：使用 sha256_hash 作为文件名，避免 latin-1 编码报错
        # 策略：filename 参数统一使用文件的 sha256_hash（确保是安全的 ASCII 字符）
        original_filename = file_record.original_filename
        
        # 将原始中文文件名（经过 URL 编码后）放在自定义 Header X-Original-Filename 中
        # 关键：必须确保编码后的值是纯 ASCII 字符（latin-1 兼容）
        from urllib.parse import quote
        # quote() 函数接受字符串参数，会自动处理 UTF-8 编码
        # 使用 safe='' 确保所有非 ASCII 字符（包括中文）都被编码为 %XX 格式
        encoded_original_name = quote(original_filename, safe='')
        
        # 严格验证：确保编码后的值可以安全地编码为 latin-1
        # 如果无法编码，说明 quote 的结果有问题，使用 base64 编码作为备选
        try:
            # 测试编码
            test_bytes = encoded_original_name.encode('latin-1')
            # 验证每个字符都是 ASCII（0-127）
            for i, char in enumerate(encoded_original_name):
                if ord(char) > 127:
                    raise ValueError(f"位置 {i} 的字符 {repr(char)} (Unicode {ord(char)}) 不是 ASCII")
        except (UnicodeEncodeError, ValueError) as e:
            safe_print(f"[警告] URL 编码结果包含非 ASCII 字符，使用 base64 编码: {safe_str(e)}")
            import base64
            # 使用 base64 编码作为备选方案
            encoded_original_name = base64.b64encode(original_filename.encode('utf-8')).decode('ascii')
        
        # 3. 构造 Header：使用 sha256_hash 作为 filename，原始文件名放在自定义 Header
        # 确保所有 header 值都是纯 ASCII 字符（latin-1 兼容）
        headers = {
            # Content-Disposition 使用 sha256_hash，确保是纯 ASCII 字符，避免 latin-1 编码错误
            "Content-Disposition": f'attachment; filename="{file_record.sha256_hash}"',
            # 将原始中文文件名（URL 编码后）放在自定义 Header 中
            "X-Original-Filename": encoded_original_name,
            # 必须暴露自定义 Header，让前端能够读取
            "Access-Control-Expose-Headers": "Content-Disposition, X-Original-Filename"
        }
        
        # 最终验证：确保所有 header 值都可以编码为 latin-1
        # 这是关键的安全检查，防止 latin-1 编码错误
        # 在传递给 FileResponse 之前，必须确保所有值都是 latin-1 安全的
        for key, value in list(headers.items()):
            try:
                # 尝试编码为 latin-1，如果失败会抛出异常
                test_bytes = value.encode('latin-1')
                # 额外验证：确保每个字符都是 ASCII（0-127）
                for i, char in enumerate(value):
                    if ord(char) > 127:
                        raise ValueError(f"位置 {i} 的字符 {repr(char)} (Unicode {ord(char)}) 不是 ASCII")
            except (UnicodeEncodeError, ValueError) as e:
                print(f"[严重错误] Header {key} 包含非 latin-1 字符")
                print(f"[严重错误] Header 值长度: {len(value)}")
                print(f"[严重错误] Header 值 (前100字符): {repr(value[:100])}")
                if isinstance(e, UnicodeEncodeError):
                    print(f"[严重错误] 错误位置: {e.start}-{e.end}")
                    print(f"[严重错误] 问题字符: {repr(value[e.start:e.end])}")
                    print(f"[严重错误] 问题字符的 Unicode 码点: {[ord(c) for c in value[e.start:e.end]]}")
                # 如果无法编码，使用 base64 编码作为最后的备选方案
                import base64
                if key == "X-Original-Filename":
                    headers[key] = base64.b64encode(original_filename.encode('utf-8')).decode('ascii')
                else:
                    # 对于其他 header，使用 ASCII 安全版本
                    headers[key] = value.encode('ascii', 'ignore').decode('ascii')
                print(f"[警告] 已将 {key} 替换为安全版本")
        
        # 最终检查：在返回 FileResponse 之前，再次验证所有 header 值
        # 这是最后一道防线，确保不会出现 latin-1 编码错误
        final_headers = {}
        for key, value in headers.items():
            # 确保值是字符串类型
            if not isinstance(value, str):
                value = str(value)
            # 再次验证可以编码为 latin-1
            try:
                value.encode('latin-1')
                final_headers[key] = value
            except UnicodeEncodeError as e:
                print(f"[致命错误] 在最终检查时发现 Header {key} 无法编码为 latin-1")
                print(f"[致命错误] 错误位置: {e.start}-{e.end}")
                print(f"[致命错误] Header 值: {repr(value)}")
                print(f"[致命错误] 问题字符: {repr(value[e.start:e.end])}")
                # 使用 base64 编码作为最后的备选方案
                import base64
                if key == "X-Original-Filename":
                    final_headers[key] = base64.b64encode(original_filename.encode('utf-8')).decode('ascii')
                else:
                    # 对于其他 header，移除所有非 ASCII 字符
                    final_headers[key] = value.encode('ascii', 'ignore').decode('ascii')
                print(f"[警告] 已将 {key} 替换为 base64/ASCII 安全版本")
        
        safe_print(f"[下载成功] 正在发送文件: {original_filename} (hash: {file_record.sha256_hash})")
        
        return FileResponse(
            path=str(file_path),
            media_type='application/octet-stream',
            headers=final_headers  # 使用经过最终验证的 headers
        )

    except HTTPException:
        raise
    except Exception as e:
        safe_print(f"_download_file_helper 发生错误: {safe_str(e)}")
        safe_print(traceback.format_exc())
        # 即使报错也要确保返回前端能识别的错误格式
        error_msg = safe_str(e)
        raise HTTPException(status_code=500, detail=f"下载文件失败: {error_msg}")


@app.get("/files/{file_id}/download")
async def download_file(
    request: Request,
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    下载文件接口（标准路径）
    
    根据文件ID从数据库查找文件信息，返回物理文件供下载。
    使用 FileResponse 确保浏览器正确下载文件并保留原始文件名。
    
    Args:
        request: FastAPI 请求对象（用于获取 origin）
        file_id: 文件ID
        db: 数据库会话
        
    Returns:
        文件流响应，包含正确的 Content-Disposition 头
        
    Raises:
        HTTPException: 如果文件不存在（404）
    """
    return _download_file_helper(file_id, db, request)


@app.get("/download/{file_id}")
async def download_file_short(
    request: Request,
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    下载文件接口（简短路径，兼容旧版本）
    
    根据文件ID从数据库查找文件信息，返回物理文件供下载。
    这是 /files/{file_id}/download 的别名接口。
    
    Args:
        request: FastAPI 请求对象（用于获取 origin）
        file_id: 文件ID
        db: 数据库会话
        
    Returns:
        文件流响应，包含正确的 Content-Disposition 头
        
    Raises:
        HTTPException: 如果文件不存在（404）
    """
    return _download_file_helper(file_id, db, request)


@app.get("/files/{file_id}/preview")
async def preview_file(
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    预览文件接口（用于图片和PDF）
    
    Args:
        file_id: 文件ID
        db: 数据库会话
        
    Returns:
        文件流响应（适合预览）
    """
    file_record = db.query(File).filter(File.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    file_path = resolve_file_path(file_record, db)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 根据文件扩展名确定媒体类型
    ext = Path(file_record.original_filename).suffix.lower()
    media_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.pdf': 'application/pdf'
    }
    media_type = media_types.get(ext, 'application/octet-stream')
    
    return FileResponse(
        path=str(file_path),
        filename=file_record.original_filename,
        media_type=media_type
    )


@app.post("/files/{file_id}/reveal")
async def reveal_file_in_explorer(
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    在**服务器所在机器**的文件管理器中定位（选中）该文件。

    说明：
    - 浏览器无法直接“跳转到本地实体文件处”（安全限制）。
    - 本接口通过后端在服务器机器上调用系统文件管理器完成定位。
    - 若你的前后端都运行在同一台 Windows 电脑上，这通常就是你想要的效果。
    """
    file_record = db.query(File).filter(File.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    file_path = resolve_file_path(file_record, db)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

    try:
        if sys.platform == "win32":
            # Windows：打开资源管理器并选中文件
            # explorer /select,"C:\path\to\file"
            subprocess.Popen(["explorer", "/select,", str(file_path)])
        elif sys.platform == "darwin":
            # macOS：Finder 中定位
            subprocess.Popen(["open", "-R", str(file_path)])
        else:
            # Linux：尽量打开所在目录（不同桌面环境不一定支持选中）
            subprocess.Popen(["xdg-open", str(file_path.parent)])

        return {"success": True, "path": str(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"定位文件失败: {safe_str(e)}")


@app.post("/files/{file_id}/open")
async def open_file_with_default_app(
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    在**服务器所在机器**上使用系统默认程序直接打开文件。

    说明：
    - 浏览器无法直接“打开用户电脑的本地实体文件”（安全限制）。
    - 本接口会在服务器机器上触发打开动作：Windows 使用默认关联程序打开。
    """
    file_record = db.query(File).filter(File.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    file_path = resolve_file_path(file_record, db)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

    # 基础安全：只允许打开存储位置目录下文件
    try:
        # 检查文件是否在允许的存储位置下
        allowed = False
        if file_record.storage_location_id:
            from backend.app.models import StorageLocation
            storage_location = db.query(StorageLocation).filter(
                StorageLocation.id == file_record.storage_location_id
            ).first()
            if storage_location:
                storage_root = Path(storage_location.path).resolve()
                resolved = file_path.resolve()
                if resolved == storage_root or storage_root in resolved.parents:
                    allowed = True
        else:
            # 兼容旧数据：检查是否在默认存储目录下
            storage_root = STORAGE_DIR.resolve()
            resolved = file_path.resolve()
            if resolved == storage_root or storage_root in resolved.parents:
                allowed = True
        
        if not allowed:
            raise HTTPException(status_code=403, detail="禁止打开非存储目录下的文件")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"路径解析失败: {safe_str(e)}")

    try:
        if sys.platform == "win32":
            os.startfile(str(file_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(file_path)])
        else:
            subprocess.Popen(["xdg-open", str(file_path)])

        return {"success": True, "path": str(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开文件失败: {safe_str(e)}")


@app.delete("/files/{file_id}")
async def delete_file(
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    删除文件接口
    
    Args:
        file_id: 文件ID
        db: 数据库会话
        
    Returns:
        删除结果
    """
    file_record = db.query(File).filter(File.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 删除物理文件
    file_path = resolve_file_path(file_record, db)
    if file_path.exists():
        try:
            os.remove(file_path)
        except Exception as e:
            safe_print(f"删除文件失败: {safe_str(e)}")
    
    # 删除数据库记录（关联的标签关系会自动删除）
    db.delete(file_record)
    db.commit()
    
    return {
        "message": "文件删除成功",
        "file_id": file_id
    }


# BatchDownloadRequest 已迁移到 backend.app.api.schemas
# 已在文件开头导入

@app.post("/files/batch-download")
async def batch_download(
    request: BatchDownloadRequest,
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    批量下载文件接口（打包成ZIP）
    
    Args:
        request: 批量下载请求，包含文件ID列表
        db: 数据库会话
        
    Returns:
        ZIP文件流
    """
    if not request.file_ids:
        raise HTTPException(status_code=400, detail="文件ID列表不能为空")
    
    # 查询所有文件
    files = db.query(File).filter(File.id.in_(request.file_ids)).all()
    if not files:
        raise HTTPException(status_code=404, detail="未找到文件")
    
    # 创建ZIP文件
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_record in files:
            file_path = Path(file_record.storage_path)
            if file_path.exists():
                zip_file.write(file_path, file_record.original_filename)
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        io.BytesIO(zip_buffer.read()),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=files.zip"
        }
    )


# UpdateFileTagsRequest 已迁移到 backend.app.api.schemas
# 已在文件开头导入

@app.put("/files/{file_id}/tags")
async def update_file_tags(
    file_id: int = PathParam(..., description="文件ID"),
    request: UpdateFileTagsRequest = ...,
    db: Session = Depends(get_db),
  # 需要登录
):
    """
    更新文件标签接口
    
    Args:
        file_id: 文件ID
        request: 标签列表
        db: 数据库会话
        
    Returns:
        更新后的文件信息
    """
    file_record = db.query(File).filter(File.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 清空现有标签
    file_record.tags.clear()
    
    # 添加新标签
    for tag_name in request.tags:
        if tag_name.strip():
            tag = get_or_create_tag(db, tag_name)
            file_record.tags.append(tag)
    
    db.commit()
    db.refresh(file_record)
    
    return {
        "id": file_record.id,
        "original_filename": file_record.original_filename,
        "tags": [tag.name for tag in file_record.tags]
    }


@app.post("/files/batch-update-tags")
async def batch_update_tags(
    file_ids: List[int] = Form(...),
    tags: List[str] = Form(...),
    db: Session = Depends(get_db)
):
    """
    批量更新文件标签接口
    
    Args:
        file_ids: 文件ID列表
        tags: 标签列表
        db: 数据库会话
        
    Returns:
        更新结果
    """
    if not file_ids:
        raise HTTPException(status_code=400, detail="文件ID列表不能为空")
    
    files = db.query(File).filter(File.id.in_(file_ids)).all()
    if not files:
        raise HTTPException(status_code=404, detail="未找到文件")
    
    # 为所有文件添加标签
    for file_record in files:
        for tag_name in tags:
            if tag_name.strip():
                tag = get_or_create_tag(db, tag_name)
                if tag not in file_record.tags:
                    file_record.tags.append(tag)
    
    db.commit()
    
    return {
        "message": "批量更新标签成功",
        "updated_count": len(files)
    }


@app.post("/files/batch-delete")
async def batch_delete_files(
    request: BatchDeleteRequest,
    db: Session = Depends(get_db)
):
    """
    批量删除文件
    """
    if not request.ids:
        raise HTTPException(status_code=400, detail="文件ID列表不能为空")
    
    files = db.query(File).filter(File.id.in_(request.ids)).all()
    if not files:
        raise HTTPException(status_code=404, detail="未找到文件")
    
    deleted_count = 0
    for file_record in files:
        # 删除物理文件
        file_path = STORAGE_DIR / file_record.storage_path
        if file_path.exists():
            try:
                os.remove(file_path)
            except Exception as e:
                safe_print(f"删除文件失败: {safe_str(e)}")
        
        # 删除数据库记录
        db.delete(file_record)
        deleted_count += 1
    
    db.commit()
    
    return {
        "message": f"成功删除 {deleted_count} 个文件",
        "deleted_count": deleted_count
    }


@app.post("/files/sync-storage")
async def sync_storage(
    db: Session = Depends(get_db)
):
    """
    核对存储：扫描 storage 目录，同步数据库记录
    - 如果数据库有记录但文件不存在，删除数据库记录
    - 如果文件存在但数据库没有记录，创建数据库记录
    """
    from backend.app.core.file_utils import calculate_sha256
    
    deleted_records = []  # 数据库有但文件不存在的记录
    added_records = []    # 文件存在但数据库没有的记录
    
    # 1. 扫描所有存储位置下的文件（递归）
    from backend.app.models import StorageLocation
    storage_files = {}  # {storage_path: (file_path, storage_location_id)}
    storage_locations = db.query(StorageLocation).filter(
        StorageLocation.enabled == True
    ).all()
    
    for storage_location in storage_locations:
        storage_dir = Path(storage_location.path)
        if storage_dir.exists():
            for file_path in storage_dir.rglob('*'):
                if file_path.is_file():
                    # 存储绝对路径
                    storage_path_str = str(file_path.resolve())
                    storage_files[storage_path_str] = (file_path, storage_location.id)
    
    safe_print(f"[核对存储] 扫描到 {len(storage_files)} 个存储文件")
    
    # 2. 查询数据库中的所有文件记录
    db_files = db.query(File).all()
    db_files_dict = {file.storage_path: file for file in db_files}
    safe_print(f"[核对存储] 数据库中有 {len(db_files)} 个文件记录")
    
    # 3. 找出数据库有但文件不存在的记录（需要删除）
    for storage_path, file_record in db_files_dict.items():
        # 使用resolve_file_path检查文件是否存在
        file_path = resolve_file_path(file_record, db)
        if not file_path.exists():
            deleted_records.append(file_record)
            safe_print(f"[核对存储] 发现孤立记录: {storage_path}")
    
    # 4. 找出文件存在但数据库没有的记录（需要添加）
    duplicate_records = []  # 文件存在但已存在于数据库（重复文件）
    for storage_path, (file_path, storage_location_id) in storage_files.items():
        if storage_path not in db_files_dict:
            try:
                # 计算文件哈希值
                sha256_hash = calculate_sha256(str(file_path))
                file_size = file_path.stat().st_size
                
                # 检查是否已存在相同哈希值的文件（重复文件）
                existing_file = db.query(File).filter(File.sha256_hash == sha256_hash).first()
                if existing_file:
                    # 发现重复文件，不添加但记录信息
                    filename = file_path.name
                    if '_' in filename:
                        original_filename = '_'.join(filename.split('_')[1:])
                    else:
                        original_filename = filename
                    
                    duplicate_records.append({
                        "storage_path": storage_path,
                        "original_filename": original_filename,
                        "file_size": file_size,
                        "existing_file": {
                            "id": existing_file.id,
                            "original_filename": existing_file.original_filename,
                            "file_size": existing_file.file_size,
                            "upload_time": existing_file.upload_time.isoformat() if existing_file.upload_time else None,
                            "tags": [tag.name for tag in existing_file.tags] if existing_file.tags else []
                        }
                    })
                    safe_print(f"[核对存储] 发现重复文件: {storage_path} (已存在文件ID: {existing_file.id}, 文件名: {existing_file.original_filename})")
                    continue
                
                # 从路径提取文件名（尝试从文件名中提取原始文件名）
                # 文件名格式：{hash_prefix}_{original_filename}
                filename = file_path.name
                if '_' in filename:
                    original_filename = '_'.join(filename.split('_')[1:])
                else:
                    original_filename = filename
                
                # 创建数据库记录
                new_file = File(
                    original_filename=original_filename,
                    storage_path=storage_path,  # 存储绝对路径
                    sha256_hash=sha256_hash,
                    file_size=file_size,
                    upload_time=datetime.utcnow(),
                    storage_location_id=storage_location_id
                )
                db.add(new_file)
                added_records.append({
                    "storage_path": storage_path,
                    "original_filename": original_filename,
                    "file_size": file_size
                })
                safe_print(f"[核对存储] 发现新文件: {storage_path}")
            except Exception as e:
                safe_print(f"[核对存储] 处理文件失败: {storage_path}, 错误: {safe_str(e)}")
                continue
    
    # 5. 执行删除和添加操作
    for file_record in deleted_records:
        db.delete(file_record)
    
    db.commit()
    
    # 构建消息
    message_parts = []
    if len(deleted_records) > 0:
        message_parts.append(f"删除 {len(deleted_records)} 个孤立记录")
    if len(added_records) > 0:
        message_parts.append(f"添加 {len(added_records)} 个新文件")
    if len(duplicate_records) > 0:
        message_parts.append(f"发现 {len(duplicate_records)} 个重复文件")
    
    if message_parts:
        message = f"存储核对完成：{', '.join(message_parts)}"
    else:
        message = "存储核对完成：未发现需要同步的记录"
    
    return {
        "message": message,
        "deleted_count": len(deleted_records),
        "added_count": len(added_records),
        "duplicate_count": len(duplicate_records),
        "deleted_records": [
            {
                "id": record.id,
                "storage_path": record.storage_path,
                "original_filename": record.original_filename
            }
            for record in deleted_records
        ],
        "added_records": added_records,
        "duplicate_records": duplicate_records
    }


# ==================== 管理后台 API（已移除） ====================


@app.get("/")
async def root():
    """
    根路径接口，返回 API 信息
    """
    return {
        "message": "团队文件管理系统 API",
        "version": "1.0.0",
        "endpoints": {
            "POST /upload": "上传文件",
            "GET /tags": "获取所有标签",
            "GET /tags/stats": "获取标签统计信息",
            "POST /tags": "创建标签",
            "PUT /tags/{id}": "更新标签",
            "DELETE /tags/{id}": "删除标签",
            "POST /tags/batch-delete": "批量删除标签",
            "POST /search": "搜索文件",
            "GET /files/{id}/download": "下载文件（标准路径）",
            "GET /download/{id}": "下载文件（简短路径）",
            "GET /files/{id}/preview": "预览文件",
            "DELETE /files/{id}": "删除文件",
            "POST /files/batch-download": "批量下载",
            "POST /files/batch-delete": "批量删除文件",
            "POST /files/sync-storage": "核对存储",
            "PUT /files/{id}/tags": "更新文件标签",
            "POST /files/batch-update-tags": "批量更新标签"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
