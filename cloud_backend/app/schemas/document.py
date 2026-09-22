"""文档、版本、标签相关 Pydantic DTO"""
from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


# ========== 标签 ==========

class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str | None = None


class TagResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    color: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ========== 版本 ==========

class VersionResponse(BaseModel):
    id: str
    document_id: str
    version_number: int
    sha256_hash: str | None
    oss_key: str | None
    file_size: int
    original_filename: str
    relative_path: str | None
    note: str | None
    is_current: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ========== 文档 ==========

class DocumentCreate(BaseModel):
    id: str                           # 与桌面端 UUID 保持一致
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    is_folder: bool = False
    status: str = "pending"
    file_count: int = 1
    total_size: int = 0


class DocumentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    file_count: int | None = None
    total_size: int | None = None


class DocumentResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str | None
    is_folder: bool
    status: str
    file_count: int
    total_size: int
    server_version: int
    uploader_id: str | None = None
    uploader_display_name: str | None = None
    created_at: datetime
    updated_at: datetime
    trashed_at: datetime | None
    tags: List[TagResponse] = []
    versions: List[VersionResponse] = []

    model_config = {"from_attributes": True}


# ========== 同步相关 ==========

class SyncPushItem(BaseModel):
    """单条推送变更"""
    entity_type: str          # document / version / tag
    entity_id: str
    operation: str            # create/update/delete/trash/restore
    payload: Dict[str, Any]
    client_version: int | None = None   # 客户端已知的 server_version（冲突检测）


class SyncPushRequest(BaseModel):
    device_id: str
    changes: List[SyncPushItem]


class ConflictItem(BaseModel):
    entity_id: str
    entity_type: str
    client_version: int
    server_version: int


class SyncPushResponse(BaseModel):
    accepted: int
    conflicts: List[ConflictItem] = []


class SyncPullResponse(BaseModel):
    changes: List[Dict[str, Any]]
    latest_sequence: int


# ========== 存储预签名 ==========

class PresignUploadRequest(BaseModel):
    workspace_id: str
    document_id: str
    version_id: str
    filename: str
    content_type: str = "application/octet-stream"


class PresignUploadResponse(BaseModel):
    upload_url: str
    oss_key: str
    expires_in: int = 3600


class PresignDownloadRequest(BaseModel):
    oss_key: str


class PresignDownloadResponse(BaseModel):
    download_url: str
    expires_in: int = 3600


class ConfirmUploadRequest(BaseModel):
    version_id: str
    oss_key: str
    sha256_hash: str
    file_size: int


# ========== 简化推送 ==========

class DocPushRequest(BaseModel):
    """本地后端推送文档到云端（创建或更新，自动归档旧版本）"""
    device_id: str
    name: str
    description: str = ""
    is_folder: bool = False
    status: str = "organized"
    file_count: int = 1
    total_size: int = 0
    tags: List[str] = []              # 标签名列表，自动合并到工作空间标签库
    # 文件版本（文件夹文档可为空）
    oss_key: str | None = None
    sha256_hash: str | None = None
    file_size: int = 0
    original_filename: str = ""
    version_note: str = ""


# ========== 设备注册 ==========

class DeviceRegisterRequest(BaseModel):
    device_id: str
    device_name: str | None = None
    platform: str | None = None


class DeviceResponse(BaseModel):
    id: str
    device_id: str
    device_name: str | None
    platform: str | None
    sync_cursors: Dict[str, int] | None
    last_seen_at: datetime

    model_config = {"from_attributes": True}
