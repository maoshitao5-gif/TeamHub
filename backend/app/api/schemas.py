"""
API 请求和响应模型（Pydantic Schemas）
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class SearchRequest(BaseModel):
    """搜索请求模型"""
    keywords: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class LoginRequest(BaseModel):
    """登录请求模型"""
    username: str
    password: str


class Token(BaseModel):
    """Token 响应模型"""
    access_token: str
    token_type: str
    username: str


class CreateUserRequest(BaseModel):
    """创建用户请求模型"""
    username: str
    password: str
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    """更新用户请求模型"""
    username: Optional[str] = None
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


class BatchDownloadRequest(BaseModel):
    """批量下载请求模型"""
    file_ids: List[int]


class UpdateFileTagsRequest(BaseModel):
    """更新文件标签请求模型"""
    tags: List[str]


class BatchUpdateTagsRequest(BaseModel):
    """批量更新标签请求模型"""
    file_ids: List[int]
    tags: List[str]


class ResetPasswordRequest(BaseModel):
    """重置密码请求模型"""
    password: str


class BatchCreateUserRequest(BaseModel):
    """批量创建用户请求模型"""
    users: List[CreateUserRequest]


class CreateStorageLocationRequest(BaseModel):
    """创建存储位置请求模型"""
    name: str
    path: str  # 绝对路径
    enabled: bool = True


class UpdateStorageLocationRequest(BaseModel):
    """更新存储位置请求模型"""
    name: Optional[str] = None
    path: Optional[str] = None
    enabled: Optional[bool] = None


class StorageLocationResponse(BaseModel):
    """存储位置响应模型"""
    id: int
    name: str
    path: str
    enabled: bool
    is_default: bool
    created_at: datetime
    
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }