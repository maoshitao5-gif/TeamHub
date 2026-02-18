"""
API 请求/响应模型
使用 Pydantic 定义数据传输对象
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


# ========== 文档相关 ==========

class DocumentCreate(BaseModel):
    """收纳文档请求"""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    is_folder: bool = False
    source_path: str = Field(..., description="文件/文件夹的原始路径")
    target_dir: Optional[str] = Field(None, description="文件库中的目标子目录（相对路径）")
    storage_mode: str = Field("move", pattern="^(move|copy|index)$")
    tags: List[str] = Field(default_factory=list, description="标签名列表")


class DocumentUpdate(BaseModel):
    """更新文档信息"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class DocumentOrganize(BaseModel):
    """整理待整理文档（从待整理区移入文件库）"""
    target_dir: str = Field(..., description="文件库中的目标子目录")
    tags: List[str] = Field(default_factory=list)
    name: Optional[str] = None


class DocumentQuickAdd(BaseModel):
    """快速放入待整理区"""
    source_path: str
    is_folder: bool = False


class BatchOrganize(BaseModel):
    """批量整理"""
    document_ids: List[str]
    target_dir: str
    tags: List[str] = Field(default_factory=list)


class BatchTagUpdate(BaseModel):
    """批量更新标签"""
    document_ids: List[str]
    add_tags: List[str] = Field(default_factory=list)
    remove_tags: List[str] = Field(default_factory=list)


class BatchDelete(BaseModel):
    """批量永久删除"""
    document_ids: List[str]


class BatchRestore(BaseModel):
    """批量恢复"""
    document_ids: List[str]


class MergeAsVersions(BaseModel):
    """合并多个文档为一个文档的多个版本"""
    document_ids: List[str] = Field(..., min_length=2)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None


# ========== 搜索相关 ==========

class SearchRequest(BaseModel):
    """搜索请求"""
    keyword: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_folder: Optional[bool] = None
    status: Optional[str] = None
    sort_by: str = Field("updated_at", pattern="^(name|updated_at|total_size|created_at)$")
    sort_order: str = Field("desc", pattern="^(asc|desc)$")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


# ========== 版本相关 ==========

class VersionCreate(BaseModel):
    """添加新版本"""
    file_path: str = Field(..., description="新版本文件的路径")
    note: Optional[str] = None
    relative_path: Optional[str] = Field(None, description="文件夹文档内的相对路径")


# ========== 标签相关 ==========

class TagCreate(BaseModel):
    """创建标签"""
    name: str = Field(..., min_length=1, max_length=100)
    color: Optional[str] = Field(None, pattern="^#[0-9a-fA-F]{6}$")


class TagUpdate(BaseModel):
    """更新标签"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    color: Optional[str] = Field(None, pattern="^#[0-9a-fA-F]{6}$")


class TagMerge(BaseModel):
    """合并标签"""
    source_tag_ids: List[str] = Field(..., min_length=1)
    target_tag_id: str


# ========== 设置相关 ==========

class LibrarySetup(BaseModel):
    """初始化文件库"""
    path: str = Field(..., description="文件库根目录路径")


class SettingsUpdate(BaseModel):
    """更新设置"""
    default_storage_mode: Optional[str] = Field(None, pattern="^(move|copy|index)$")
    max_versions: Optional[int] = Field(None, ge=0)
    max_version_age_days: Optional[int] = Field(None, ge=0)
    trash_auto_clean_days: Optional[int] = Field(None, ge=0)


# ========== 响应模型 ==========

class TagResponse(BaseModel):
    id: str
    name: str
    color: Optional[str] = None
    document_count: int = 0

    class Config:
        from_attributes = True


class VersionResponse(BaseModel):
    id: str
    version_number: int
    file_path: str
    relative_path: Optional[str] = None
    sha256_hash: Optional[str] = None
    file_size: int
    original_filename: str
    note: Optional[str] = None
    is_current: bool
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    is_folder: bool
    storage_path: Optional[str] = None
    storage_mode: str
    original_path: Optional[str] = None
    status: str
    file_count: int
    total_size: int
    created_at: datetime
    updated_at: datetime
    tags: List[TagResponse] = []
    current_version: Optional[VersionResponse] = None

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    documents: List[DocumentResponse]
    total: int
    page: int
    page_size: int


class PendingCountResponse(BaseModel):
    count: int


class LibraryInfoResponse(BaseModel):
    path: Optional[str] = None
    initialized: bool
    total_documents: int = 0
    total_size: int = 0
    pending_count: int = 0


# ========== 智能分析相关 ==========

class SimilarGroupDocument(BaseModel):
    id: str
    name: str
    total_size: int
    created_at: datetime

class SimilarGroup(BaseModel):
    base_name: str
    documents: List[SimilarGroupDocument]
    suggested_name: str

class TagSuggestion(BaseModel):
    tag: str
    document_ids: List[str]
    reason: str

class PendingAnalysisResponse(BaseModel):
    version_groups: List[SimilarGroup]
    tag_suggestions: List[TagSuggestion]


# ========== 文件缺失重定位 ==========

class DocumentRelocate(BaseModel):
    new_path: str = Field(..., description="新的文件路径")
