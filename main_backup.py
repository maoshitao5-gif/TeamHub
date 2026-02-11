"""
FastAPI 应用主入口
提供文件上传、标签管理和文件搜索等核心功能
"""
import os
import sys
import hashlib
import shutil
import urllib.parse
import time
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

# ========== 全局 UTF-8 编码配置 ==========
# 强制设置标准输出、标准错误和标准输入使用 UTF-8 编码
# 这对于 Windows 系统特别重要，因为默认编码可能是 GBK
if sys.platform == 'win32':
    # Windows 系统：设置控制台编码为 UTF-8
    try:
        # 设置环境变量
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        # 尝试设置控制台编码（如果支持）
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        # 如果设置失败，继续执行（某些环境可能不支持）
        pass

# 设置默认编码为 UTF-8（Python 3.7+）
import locale
try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except locale.Error:
        # 如果都失败，使用系统默认
        pass
# ========== 全局 UTF-8 编码配置结束 ==========

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, Path as PathParam, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from jose import JWTError, jwt
from passlib.context import CryptContext
import zipfile
import io
import traceback

from database import get_db, init_db
from models import File, Tag, file_tag_association, User

# JWT 配置
SECRET_KEY = "your-secret-key-change-this-in-production-please-use-env-variable"  # 生产环境应使用环境变量
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30 * 24 * 60  # 30天过期

# 密码加密上下文
# 使用bcrypt算法，设置rounds参数以兼容不同版本的bcrypt
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12  # 明确指定rounds参数
)

# HTTP Bearer Token 安全方案
security = HTTPBearer()


class SearchRequest(BaseModel):
    """
    搜索请求模型
    用于定义搜索接口的请求体结构
    """
    keywords: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class LoginRequest(BaseModel):
    """
    登录请求模型
    """
    username: str
    password: str


class Token(BaseModel):
    """
    Token 响应模型
    """
    access_token: str
    token_type: str
    username: str


# 密码验证和加密函数
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    # bcrypt限制密码长度不能超过72字节，需要截断
    if len(password.encode('utf-8')) > 72:
        password = password[:72]
    return pwd_context.hash(password)


# JWT Token 生成和验证
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """验证令牌"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


# 认证依赖
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    获取当前登录用户
    从请求头中提取 token 并验证
    """
    token = credentials.credentials
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=401,
            detail="无效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# 管理员权限检查依赖
async def get_admin_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    检查当前用户是否为超级管理员
    只有超级管理员才能访问管理后台
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="权限不足，需要超级管理员权限"
        )
    return current_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    在应用启动时初始化数据库，在关闭时执行清理操作
    """
    # 启动时执行
    init_db()
    print("数据库初始化完成")
    
    # 初始化默认管理员用户（如果不存在）
    db = next(get_db())
    try:
        default_user = db.query(User).filter(User.username == "admin").first()
        if not default_user:
            # 创建默认超级管理员用户，密码为 admin123
            default_password_hash = get_password_hash("admin123")
            default_user = User(
                username="admin",
                hashed_password=default_password_hash,
                created_at=datetime.utcnow(),
                is_admin=True  # 设置为超级管理员
            )
            db.add(default_user)
            db.commit()
            safe_print("[OK] 已创建默认超级管理员用户: admin / admin123")
        else:
            # 如果admin用户已存在但is_admin为False，更新为True
            if not default_user.is_admin:
                default_user.is_admin = True
                db.commit()
                safe_print("[OK] 已将admin用户升级为超级管理员")
            else:
                safe_print("[OK] 默认超级管理员用户已存在")
    except Exception as e:
        safe_print(f"初始化默认用户失败: {safe_str(e)}")
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

# 添加全局中间件，确保所有响应都使用 UTF-8 编码
@app.middleware("http")
async def add_utf8_header(request: Request, call_next):
    """确保所有响应都包含 UTF-8 编码头"""
    response = await call_next(request)
    # 确保 Content-Type 包含 charset=utf-8（对于文本响应）
    if "content-type" in response.headers:
        content_type = response.headers["content-type"]
        if "application/json" in content_type and "charset" not in content_type:
            response.headers["content-type"] = content_type.replace(
                "application/json", "application/json; charset=utf-8"
            )
        elif "text/" in content_type and "charset" not in content_type:
            response.headers["content-type"] = content_type + "; charset=utf-8"
    return response

# 配置 CORS，允许前端跨域访问
# 开发环境：允许所有 localhost 和 127.0.0.1 的端口
# 生产环境应限制具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 全局异常处理器，确保所有错误响应都包含 CORS 头
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """处理 HTTP 异常，确保包含 CORS 头"""
    # 获取允许的 origin
    origin = request.headers.get("origin")
    allowed_origins = [
        "http://localhost:5173", 
        "http://localhost:3000", 
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]
    
    # 对于开发环境，允许所有 localhost 和 127.0.0.1 的请求
    if origin and (origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")):
        cors_origin = origin
    elif origin and origin in allowed_origins:
        cors_origin = origin
    else:
        cors_origin = allowed_origins[0] if allowed_origins else "http://localhost:5173"
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理请求验证错误，确保包含 CORS 头"""
    # 获取允许的 origin
    origin = request.headers.get("origin")
    allowed_origins = [
        "http://localhost:5173", 
        "http://localhost:3000", 
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]
    
    # 对于开发环境，允许所有 localhost 和 127.0.0.1 的请求
    if origin and (origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")):
        cors_origin = origin
    elif origin and origin in allowed_origins:
        cors_origin = origin
    else:
        cors_origin = allowed_origins[0] if allowed_origins else "http://localhost:5173"
    
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
        headers={
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理所有未捕获的异常，确保包含 CORS 头"""
    import traceback
    error_detail = safe_str(exc)
    error_traceback = traceback.format_exc()
    safe_print(f"未捕获的异常: {error_detail}")
    safe_print(f"错误堆栈: {error_traceback}")
    
    # 获取允许的 origin
    origin = request.headers.get("origin")
    allowed_origins = [
        "http://localhost:5173", 
        "http://localhost:3000", 
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]
    
    # 对于开发环境，允许所有 localhost 和 127.0.0.1 的请求
    if origin and (origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")):
        cors_origin = origin
    elif origin and origin in allowed_origins:
        cors_origin = origin
    else:
        cors_origin = allowed_origins[0] if allowed_origins else "http://localhost:5173"
    
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误: {error_detail}"},
        headers={
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

# 文件存储目录
STORAGE_DIR = Path("storage")
STORAGE_DIR.mkdir(exist_ok=True)  # 如果目录不存在则创建


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


def calculate_sha256(file_path: str) -> str:
    """
    计算文件的 SHA-256 哈希值
    
    Args:
        file_path: 文件路径
        
    Returns:
        文件的 SHA-256 哈希值（十六进制字符串）
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # 分块读取文件，避免大文件占用过多内存
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def calculate_folder_content_hash(zip_path: str) -> str:
    """
    计算文件夹压缩包的内容哈希值（基于文件夹内所有文件的内容）
    忽略文件夹名称和ZIP结构，只关注文件内容
    
    Args:
        zip_path: ZIP 文件路径
        
    Returns:
        文件夹内容的组合哈希值（十六进制字符串）
    """
    import tempfile
    
    # 创建临时目录用于解压
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        
        print(f"[内容查重] 解压 ZIP 文件到临时目录: {temp_dir_path}")
        
        # 解压 ZIP 文件
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir_path)
        
        # 收集所有文件（排除文件夹）
        file_hashes = []
        
        # 递归遍历解压后的目录
        for root, dirs, files in os.walk(temp_dir_path):
            for file_name in files:
                file_path = Path(root) / file_name
                # 计算每个文件内容的哈希值
                file_hash = calculate_sha256(str(file_path))
                file_hashes.append(file_hash)
                safe_print(f"[内容查重] 文件: {file_path.relative_to(temp_dir_path)} -> 哈希: {file_hash[:16]}...")
        
        if not file_hashes:
            raise ValueError("ZIP 文件中没有文件")
        
        # 对文件哈希值进行排序，确保顺序一致（忽略文件路径）
        file_hashes.sort()
        
        print(f"[内容查重] 共找到 {len(file_hashes)} 个文件")
        print(f"[内容查重] 文件哈希值列表（已排序）:")
        for i, fh in enumerate(file_hashes[:10]):  # 只显示前10个
            print(f"  [{i+1}] {fh[:16]}...")
        if len(file_hashes) > 10:
            print(f"  ... 还有 {len(file_hashes) - 10} 个文件")
        
        # 组合所有文件哈希值，计算最终的组合哈希值
        combined_hash = hashlib.sha256()
        for file_hash in file_hashes:
            combined_hash.update(file_hash.encode('utf-8'))
        
        content_hash = combined_hash.hexdigest()
        print(f"[内容查重] 文件夹内容组合哈希值: {content_hash}")
        print(f"[内容查重] 组合哈希值前16位: {content_hash[:16]}...")
        
        return content_hash


def get_or_create_tag(db: Session, tag_name: str) -> Tag:
    """
    获取或创建标签
    如果标签已存在则返回，不存在则创建
    
    Args:
        db: 数据库会话
        tag_name: 标签名称
        
    Returns:
        Tag 对象
    """
    # 规范化标签名称：去除首尾空格，转为小写（可选，根据需求决定是否区分大小写）
    tag_name = tag_name.strip().lower()
    
    # 查找是否已存在该标签
    tag = db.query(Tag).filter(Tag.name == tag_name).first()
    
    if not tag:
        # 如果不存在则创建新标签
        tag = Tag(name=tag_name)
        db.add(tag)
        db.commit()
        db.refresh(tag)
    
    return tag


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(),
    tags: List[str] = Form(),
    is_folder_archive: Optional[str] = Form(None),  # 标识是否为文件夹压缩包
    folder_name: Optional[str] = Form(None),  # 文件夹名称
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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
        
        # 生成存储路径：使用哈希值的前8位 + 原始文件名，避免文件名冲突
        # 使用安全的文件名，避免特殊字符导致编码错误
        file_extension = Path(safe_filename).suffix
        storage_filename = f"{sha256_hash[:8]}_{safe_filename}"
        storage_path = STORAGE_DIR / storage_filename
        
        # 将临时文件移动到最终存储位置
        shutil.move(str(temp_file_path), str(storage_path))
        
        # 文件大小已在之前检查时获取，直接使用
        
        # 创建文件记录
        # 如果是文件夹压缩包，将文件夹名称存储在 relative_path 字段中
        relative_path_value = None
        if is_folder_archive == 'true' and folder_name:
            relative_path_value = folder_name
        
        db_file = File(
            original_filename=file.filename,
            storage_path=str(storage_path),
            sha256_hash=sha256_hash,
            file_size=file_size,
            upload_time=datetime.utcnow(),
            relative_path=relative_path_value
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
                # 如果 relative_path 不为空，在 storage 目录下创建对应的文件夹结构
                if relative_path and relative_path.strip():
                    # 规范化相对路径（去除开头的 / 或 \）
                    normalized_path = relative_path.strip().lstrip('/\\')
                    # 获取文件的目录部分（去除文件名）
                    path_obj = Path(normalized_path)
                    if path_obj.parent and path_obj.parent != Path('.'):
                        # 创建文件夹结构（相对于 storage 目录）
                        folder_path = STORAGE_DIR / path_obj.parent
                        folder_path.mkdir(parents=True, exist_ok=True)
                        # 存储文件名使用哈希值前缀 + 原始文件名
                        storage_filename = f"{sha256_hash[:8]}_{path_obj.name}"
                        storage_path = folder_path / storage_filename
                    else:
                        # 文件在根目录，直接存储在 storage 根目录
                        storage_filename = f"{sha256_hash[:8]}_{path_obj.name}"
                        storage_path = STORAGE_DIR / storage_filename
                else:
                    # 单文件上传模式，直接存储在 storage 根目录
                    storage_filename = f"{sha256_hash[:8]}_{file.filename}"
                    storage_path = STORAGE_DIR / storage_filename
                
                # 将临时文件移动到最终存储位置
                shutil.move(str(temp_file_path), str(storage_path))
                
                # 创建文件记录
                db_file = File(
                    original_filename=file.filename,
                    storage_path=str(storage_path),
                    sha256_hash=sha256_hash,
                    file_size=file_size,
                    upload_time=datetime.utcnow(),
                    relative_path=relative_path if relative_path and relative_path.strip() else None
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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


@app.post("/login")
async def login(
    login_request: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    用户登录接口
    
    Args:
        login_request: 登录请求，包含用户名和密码
        db: 数据库会话
        
    Returns:
        Token 信息，包含 access_token 和 token_type
    """
    # 查询用户
    user = db.query(User).filter(User.username == login_request.username).first()
    
    # 验证用户名和密码
    if not user or not verify_password(login_request.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误"
        )
    
    # 更新最后登录时间
    user.last_login = datetime.utcnow()
    db.commit()
    
    # 创建访问令牌
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username
    }


@app.get("/auth/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    获取当前登录用户信息
    
    Args:
        current_user: 当前登录用户（通过认证依赖注入）
        
    Returns:
        当前用户信息
    """
    return {
        "id": current_user.id,
        "username": current_user.username,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "last_login": current_user.last_login.isoformat() if current_user.last_login else None
    }


@app.get("/tags/stats")
async def get_tags_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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


@app.post("/search")
async def search_files(
    search_request: SearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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
    current_user: User = Depends(get_current_user)  # 需要登录
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
    current_user: User = Depends(get_current_user)  # 需要登录
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
    current_user: User = Depends(get_current_user)  # 需要登录
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
    
    file_path = Path(file_record.storage_path)
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


@app.delete("/files/{file_id}")
async def delete_file(
    file_id: int = PathParam(..., description="文件ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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
    file_path = Path(file_record.storage_path)
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


class BatchDownloadRequest(BaseModel):
    """批量下载请求模型"""
    file_ids: List[int]


@app.post("/files/batch-download")
async def batch_download(
    request: BatchDownloadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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


class UpdateFileTagsRequest(BaseModel):
    """更新文件标签请求模型"""
    tags: List[str]


@app.put("/files/{file_id}/tags")
async def update_file_tags(
    file_id: int = PathParam(..., description="文件ID"),
    request: UpdateFileTagsRequest = ...,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)  # 需要登录
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


# ==================== 管理后台 API ====================

class CreateUserRequest(BaseModel):
    """创建用户请求模型"""
    username: str
    password: str
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    """更新用户请求模型"""
    password: Optional[str] = None
    is_admin: Optional[bool] = None


class CreateTagRequest(BaseModel):
    """创建标签请求模型"""
    name: str


class UpdateTagRequest(BaseModel):
    """更新标签请求模型"""
    name: str


class BatchDeleteRequest(BaseModel):
    """批量删除请求模型"""
    ids: List[int]


# ==================== 文件管理 API ====================

@app.get("/admin/files")
async def admin_get_all_files(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员获取所有文件列表（分页）
    """
    offset = (page - 1) * page_size
    files = db.query(File).order_by(File.upload_time.desc()).offset(offset).limit(page_size).all()
    total = db.query(File).count()
    
    return {
        "files": [
            {
                "id": file.id,
                "original_filename": file.original_filename,
                "file_size": file.file_size,
                "sha256_hash": file.sha256_hash,
                "upload_time": file.upload_time.isoformat() if file.upload_time else None,
                "relative_path": file.relative_path,
                "tags": [tag.name for tag in file.tags]
            }
            for file in files
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


@app.post("/admin/files/batch-delete")
async def admin_batch_delete_files(
    request: BatchDeleteRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员批量删除文件
    """
    if not request.ids:
        raise HTTPException(status_code=400, detail="文件ID列表不能为空")
    
    files = db.query(File).filter(File.id.in_(request.ids)).all()
    if not files:
        raise HTTPException(status_code=404, detail="未找到文件")
    
    deleted_count = 0
    for file_record in files:
        # 删除物理文件
        file_path = Path(file_record.storage_path)
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


# ==================== 标签管理 API ====================

@app.get("/admin/tags")
async def admin_get_all_tags(
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员获取所有标签（包含统计信息）
    """
    tags = db.query(Tag).all()
    
    tag_list = []
    for tag in tags:
        file_count = len(tag.files) if tag.files else 0
        tag_list.append({
            "id": tag.id,
            "name": tag.name,
            "file_count": file_count
        })
    
    tag_list.sort(key=lambda x: (-x["file_count"], x["name"]))
    
    return {
        "tags": tag_list,
        "total": len(tag_list)
    }


@app.post("/admin/tags")
async def admin_create_tag(
    request: CreateTagRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员创建标签
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


@app.put("/admin/tags/{tag_id}")
async def admin_update_tag(
    tag_id: int,
    request: UpdateTagRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员更新标签名称
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


@app.delete("/admin/tags/{tag_id}")
async def admin_delete_tag(
    tag_id: int,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员删除标签（会解除所有文件的关联）
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


@app.post("/admin/tags/batch-delete")
async def admin_batch_delete_tags(
    request: BatchDeleteRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员批量删除标签
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


# ==================== 用户管理 API ====================

@app.get("/admin/users")
async def admin_get_all_users(
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员获取所有用户列表
    """
    users = db.query(User).order_by(User.created_at.desc()).all()
    
    return {
        "users": [
            {
                "id": user.id,
                "username": user.username,
                "is_admin": user.is_admin,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login": user.last_login.isoformat() if user.last_login else None
            }
            for user in users
        ],
        "total": len(users)
    }


@app.post("/admin/users")
async def admin_create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员创建新用户
    """
    username = request.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    
    # 检查用户名是否已存在
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    # 验证密码
    if not request.password or len(request.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少6位")
    
    # 创建用户
    password_hash = get_password_hash(request.password)
    new_user = User(
        username=username,
        hashed_password=password_hash,
        is_admin=request.is_admin,
        created_at=datetime.utcnow()
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {
        "id": new_user.id,
        "username": new_user.username,
        "is_admin": new_user.is_admin,
        "message": "用户创建成功"
    }


@app.put("/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员更新用户信息（密码或权限）
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 不能修改自己的管理员权限（防止误操作）
    if user.id == admin_user.id and request.is_admin is not None and not request.is_admin:
        raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
    
    # 更新密码
    if request.password:
        if len(request.password) < 6:
            raise HTTPException(status_code=400, detail="密码长度至少6位")
        user.hashed_password = get_password_hash(request.password)
    
    # 更新管理员权限
    if request.is_admin is not None:
        user.is_admin = request.is_admin
    
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "message": "用户更新成功"
    }


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员删除用户
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 不能删除自己
    if user.id == admin_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    
    db.delete(user)
    db.commit()
    
    return {
        "message": "用户删除成功",
        "deleted_user_id": user_id
    }


class ResetPasswordRequest(BaseModel):
    """重置密码请求模型"""
    password: str


@app.post("/admin/users/{user_id}/reset-password")
async def admin_reset_user_password(
    user_id: int,
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员重置用户密码
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 验证密码
    if not request.password or len(request.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少6位")
    
    # 重置密码
    user.hashed_password = get_password_hash(request.password)
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "username": user.username,
        "message": "密码重置成功"
    }


class BatchCreateUserRequest(BaseModel):
    """批量创建用户请求模型"""
    users: List[CreateUserRequest]


@app.post("/admin/users/batch-create")
async def admin_batch_create_users(
    request: BatchCreateUserRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(get_admin_user)
):
    """
    管理员批量创建用户
    """
    if not request.users:
        raise HTTPException(status_code=400, detail="用户列表不能为空")
    
    created_users = []
    failed_users = []
    
    for user_request in request.users:
        try:
            username = user_request.username.strip()
            if not username:
                failed_users.append({
                    "username": username,
                    "error": "用户名不能为空"
                })
                continue
            
            # 检查用户名是否已存在
            existing_user = db.query(User).filter(User.username == username).first()
            if existing_user:
                failed_users.append({
                    "username": username,
                    "error": "用户名已存在"
                })
                continue
            
            # 验证密码
            if not user_request.password or len(user_request.password) < 6:
                failed_users.append({
                    "username": username,
                    "error": "密码长度至少6位"
                })
                continue
            
            # 创建用户
            password_hash = get_password_hash(user_request.password)
            new_user = User(
                username=username,
                hashed_password=password_hash,
                is_admin=user_request.is_admin,
                created_at=datetime.utcnow()
            )
            db.add(new_user)
            db.flush()
            
            created_users.append({
                "id": new_user.id,
                "username": new_user.username,
                "is_admin": new_user.is_admin
            })
        except Exception as e:
            failed_users.append({
                "username": user_request.username if user_request else "未知",
                "error": str(e)
            })
    
    db.commit()
    
    return {
        "message": f"成功创建 {len(created_users)} 个用户，失败 {len(failed_users)} 个",
        "created_count": len(created_users),
        "failed_count": len(failed_users),
        "created_users": created_users,
        "failed_users": failed_users
    }


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
