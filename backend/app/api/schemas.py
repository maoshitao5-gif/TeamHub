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
    tags: List[str] = Field(..., min_items=1, description="标签名列表（至少一个）")
    cloud_source: Optional[str] = Field(None, description="来源标记，如 'cloud_import'")
    cloud_doc_id: Optional[str] = Field(None, description="从云仓库导入时对应的云端文档ID")


class DocumentUpdate(BaseModel):
    """更新文档信息"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class DocumentOrganize(BaseModel):
    """整理待整理文档（从待整理区移入文件库）"""
    target_dir: str = Field(..., description="文件库中的目标子目录")
    tags: List[str] = Field(..., min_items=1, description="标签名列表（至少一个）")
    name: Optional[str] = None


class DocumentQuickAdd(BaseModel):
    """快速放入待整理区"""
    source_path: str
    is_folder: bool = False


class BatchOrganize(BaseModel):
    """批量整理"""
    document_ids: List[str]
    target_dir: str
    tags: List[str] = Field(..., min_items=1, description="标签名列表（至少一个）")


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
    folder: Optional[str] = None  # 按目录前缀过滤（None=不过滤，"."=根目录文档，其他=子目录路径）
    sort_by: str = Field("updated_at", pattern="^(name|updated_at|total_size|created_at)$")
    sort_order: str = Field("desc", pattern="^(asc|desc)$")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


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


class MkdirRequest(BaseModel):
    """在文件库中创建目录"""
    path: str = Field(..., description="相对于文件库的目录路径，如 'docs/项目A'")


class PendingPathUpdate(BaseModel):
    """修改待整理文件夹名称"""
    new_folder_name: str = Field(..., min_length=1, max_length=100, description="新的待整理文件夹名称")
    migrate: bool = Field(..., description="True=迁移文件并更新记录，False=仅清空数据库记录（保留实体文件）")


class SettingsUpdate(BaseModel):
    """更新设置"""
    default_storage_mode: Optional[str] = Field(None, pattern="^(move|copy|index)$")
    trash_auto_clean_days: Optional[int] = Field(None, ge=0)
    max_file_size: Optional[int] = Field(None, ge=0)
    on_conflict: Optional[str] = Field(None, pattern="^(rename|version)$")
    default_sort_by: Optional[str] = None
    default_sort_order: Optional[str] = Field(None, pattern="^(asc|desc)$")
    auto_scan_on_startup: Optional[bool] = None
    items_per_page: Optional[int] = Field(None, ge=1, le=100)
    enable_floating_window: Optional[bool] = None
    library_show_flat_view: Optional[bool] = None
    library_show_tree_view: Optional[bool] = None
    cloud_api_url: Optional[str] = None


# ========== 同步相关 ==========

class DocSyncRequest(BaseModel):
    """单文档智能同步请求"""
    doc_id: str
    force: bool = False  # 冲突时用户已确认，强制推送


# ========== 响应模型 ==========

class TagResponse(BaseModel):
    id: str
    name: str
    color: Optional[str] = None
    document_count: int = 0

    class Config:
        from_attributes = True


class TagListResponse(BaseModel):
    """标签列表响应，包含标签列表和总唯一文档数"""
    tags: list[TagResponse]
    total_unique_documents: int


class VersionCreate(BaseModel):
    file_path: str
    relative_path: Optional[str] = None
    note: Optional[str] = None


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
    sync_status: str = "local"
    cloud_sync_enabled: bool = False
    last_synced_at: Optional[datetime] = None
    # 变动追踪字段
    local_modified: bool = False
    cloud_updated_at: Optional[datetime] = None
    # 来源标记
    cloud_source: Optional[str] = None
    # 云仓库导入关联 ID
    cloud_doc_id: Optional[str] = None
    # 新版推送记录
    cloud_pushed_at: Optional[datetime] = None
    cloud_hash: Optional[str] = None
    # 云端工作空间
    workspace_id: Optional[str] = None

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
    trash_count: int = 0
    trash_size: int = 0
    pending_folder_name: str = "待整理"


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


# ========== 查重相关 ==========

class DuplicateCheckRequest(BaseModel):
    """单文件查重请求（file_path 与 sha256_hash 二选一）"""
    file_path: Optional[str] = Field(None, description="待查重文件的绝对路径（与 sha256_hash 二选一）")
    sha256_hash: Optional[str] = Field(None, description="已知哈希，跳过文件计算（与 file_path 二选一）")
    exclude_doc_id: Optional[str] = Field(None, description="排除指定文档（用于推送查重时排除自身）")


class DuplicateCheckResponse(BaseModel):
    """单文件查重响应"""
    is_duplicate: bool
    existing_document: Optional[DocumentResponse] = None  # 在已整理文档库中找到的重复
    sha256_hash: Optional[str] = None
    # False 表示查重本身出错（文件不可读等），前端应 fallthrough
    check_succeeded: bool = True
    # 在待整理区中找到的重复（区别于已整理文档库）
    duplicate_in_pending: bool = False
    pending_document: Optional[DocumentResponse] = None


class DuplicateDetail(BaseModel):
    """文件夹查重中单个重复文件的详情"""
    source_filename: str            # 相对于文件夹的源文件名（或绝对路径末段）
    existing_document_id: str       # 已有文档 ID
    existing_document_name: str     # 已有文档名称
    existing_document_status: str = "organized"  # 已有文档状态（organized/pending）


class FolderDuplicateCheckRequest(BaseModel):
    """文件夹查重请求（不打散模式）"""
    folder_path: str = Field(..., description="文件夹的绝对路径（尚未移动）")


class FolderDuplicateCheckResponse(BaseModel):
    """文件夹查重响应"""
    total_files: int = 0            # 文件夹内总文件数
    duplicate_count: int = 0        # 命中已整理文档的文件数
    duplicates: List[DuplicateDetail] = []  # 具体重复文件详情列表
    has_pending_hashes: bool = False        # 是否存在哈希未计算完成的已整理文档（查重结果可能不完整）
    check_succeeded: bool = True


# ========== 同步相关 ==========

class DeviceNameUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="设备名称")


class CloudLoginRequest(BaseModel):
    """云端账号登录请求"""
    email: str = Field(..., description="邮箱")
    password: str = Field(..., description="密码")


class CloudWorkspaceRequest(BaseModel):
    """绑定工作空间请求"""
    workspace_id: str
    workspace_name: str


class SyncTokenRequest(BaseModel):
    """前端同步 token 到本地后端请求"""
    access_token: str
    refresh_token: Optional[str] = None


class CloudPushRequest(BaseModel):
    """推送到云端请求"""
    workspace_id: Optional[str] = None      # 缺省读 config
    doc_ids: Optional[List[str]] = None     # 缺省推送全部待推送


class CloudPullRequest(BaseModel):
    """从云端拉取请求"""
    workspace_id: Optional[str] = None
    since_sequence: Optional[int] = None    # 缺省读 config.sync_cursors


class CloudConflictResolveRequest(BaseModel):
    """冲突解决请求"""
    doc_id: str
    choice: str = Field(..., description="keep_local | use_remote | keep_both")


class DocReplaceRequest(BaseModel):
    """文件替换请求"""
    doc_id: str


# ========== OSS 云同步相关 ==========

class OSSConfigUpdate(BaseModel):
    """保存 OSS 配置"""
    oss_endpoint: str
    oss_access_key_id: str
    oss_access_key_secret: str
    oss_bucket_name: str
    oss_prefix: str = "teamhub"


class OSSPushRequest(BaseModel):
    """推送请求：None 表示推送全部已启用且待同步的文档"""
    doc_ids: Optional[List[str]] = None


class OSSPullRequest(BaseModel):
    """拉取请求：None 表示拉取所有其他设备"""
    device_ids: Optional[List[str]] = None


class ConflictResolveRequest(BaseModel):
    """冲突解决请求"""
    choice: str  # keep_local | use_remote | keep_both
    remote_device_id: Optional[str] = None


class InboxImportRequest(BaseModel):
    """收件箱导入请求：将共享文件导入到本地文档库"""
    device_id: str
    doc_id: str
    filename: str
    name: Optional[str] = None                         # 用户指定文档名（不含扩展名），空则使用原文件名
    target_dir: str = ""                                # 目标子目录（相对文件库），空字符串=文件库根目录
    tags: List[str] = Field(default_factory=list)       # 标签名列表（至少一个）


class InboxDownloadRequest(BaseModel):
    """收件箱下载请求：将共享文件下载到指定本地路径"""
    device_id: str
    doc_id: str
    filename: str
    save_path: str


class QuickShareRequest(BaseModel):
    """一键共享请求：开启云同步 + 设置可见性 + 立即推送"""
    visibility: str = Field("public", pattern="^(private|public)$")


# ========== 新版简化同步 ==========

class PushDocRequest(BaseModel):
    """推送单个文档到云端"""
    doc_id: str
    version_note: Optional[str] = None   # 本次推送的版本备注（可选）


class PullDocRequest(BaseModel):
    """从云端拉取单个文档到本地"""
    workspace_id: str
    cloud_doc_id: str
    target_dir: str = ""                  # 拉取到本地的目标子目录（相对文件库根，空=根目录）


class CheckDocRequest(BaseModel):
    """检查单个文档本地与云端差异"""
    doc_id: str


class BackupInfo(BaseModel):
    """云端备份记录"""
    id: str
    device_id: str
    device_name: str
    filename: str
    file_size: int
    created_at: Optional[str] = None


class RestoreRequest(BaseModel):
    """从云端恢复备份的请求"""
    backup_id: str
    confirmed: bool = False    # False=仅检查是否有现有数据，True=执行覆盖恢复
