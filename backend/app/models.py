"""
数据模型模块
定义 Document、Version、Tag 表结构和关系
使用 UUID v7 作为主键，为未来远端同步预留
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, BigInteger, Integer, Boolean, Table, ForeignKey, Text, Float, UniqueConstraint
from sqlalchemy.orm import relationship
from backend.app.database import Base


def generate_uuid() -> str:
    """生成 UUID 字符串（优先 UUID v7，回退到 UUID v4）"""
    if hasattr(uuid, 'uuid7'):
        return str(uuid.uuid7())
    # Python < 3.13.1 回退到 uuid4（仍然全局唯一，只是不可排序）
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """获取当前 UTC 时间"""
    return datetime.now(timezone.utc)


# ========== 文档-标签 多对多关联表 ==========
document_tag = Table(
    'document_tag',
    Base.metadata,
    Column('document_id', String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True),
    Column('tag_id', String(36), ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)
)


class Document(Base):
    """
    文档表 —— 管理的最小单位
    一个文档可以是单个文件，也可以是一个文件夹
    """
    __tablename__ = "documents"

    # UUID v7 主键
    id = Column(String(36), primary_key=True, default=generate_uuid)

    # 文档名称（用户可修改）
    name = Column(String(255), nullable=False, index=True)

    # 描述/备注（可选）
    description = Column(Text, nullable=True)

    # 是否为文件夹文档
    is_folder = Column(Boolean, default=False, nullable=False)

    # 资料类型: file / folder / archive(M1 新增,由 is_folder 迁移;契约 §2.1)
    doc_type = Column(String(10), nullable=False, default="file")

    # 当前版本 ID —— 唯一事实来源(M1 新增;契约 §1,替代 is_current 的读方职责)
    # FK 保证存在性(不存在的版本 ID 不能落库);归属校验由写入路径承担(M1-R01)
    current_version_id = Column(String(36), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    version_sequence = Column(Integer, nullable=False, default=0)

    # 文件库中的存储路径（相对于文件库根目录）
    storage_path = Column(String(1000), nullable=True)
    storage_area = Column(String(10), nullable=False, default="library")

    # 收纳方式: move / copy / index
    storage_mode = Column(String(10), nullable=False, default="move")

    # 原始路径（收纳前的位置，用于 index 模式和追溯）
    original_path = Column(String(1000), nullable=True)

    # 状态: organized / pending / missing / trashed
    status = Column(String(20), nullable=False, default="pending", index=True)

    # 包含的文件数（单文件=1，文件夹=内部文件数）
    file_count = Column(Integer, default=1, nullable=False)

    # 总大小（字节）
    total_size = Column(BigInteger, default=0, nullable=False)

    # 时间戳（全部 UTC）
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    trashed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)

    # 同步状态：local（本地未同步）/ synced（已同步）/ conflict（存在冲突）
    sync_status = Column(String(10), nullable=False, default="local", index=True)

    # 最后成功同步时间（UTC），NULL 表示从未同步
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    # 是否启用云同步（每文档独立开关）
    cloud_sync_enabled = Column(Boolean, default=False, nullable=False)

    # 共享可见性："private"（仅自备份，不进 shared/）/"public"（全员可见）
    share_visibility = Column(String(10), nullable=False, default="public")

    # 云端服务器版本号（冲突检测，对应 CloudDocument.server_version，NULL=从未同步）
    server_version = Column(Integer, nullable=True)

    # 所属云端工作空间（NULL=未关联）
    workspace_id = Column(String(36), nullable=True, index=True)

    # 本地文件已被修改（与上次同步版本的哈希不一致），NULL/False=未修改
    local_modified = Column(Boolean, default=False, nullable=False)

    # 云端文件上次有更新推入本地的时间；非NULL表示用户尚未确认该云端更新
    cloud_updated_at = Column(DateTime(timezone=True), nullable=True)

    # 文档来源标记：NULL=本地创建，"cloud_import"=从云仓库导入
    cloud_source = Column(String(20), nullable=True)

    # 从云仓库导入时对应的云端 doc_id（用于哈希对比关联，NULL=本地创建）
    cloud_doc_id = Column(String(36), nullable=True, index=True)

    # 最后一次成功推送到云端的时间（NULL=从未推送）
    cloud_pushed_at = Column(DateTime(timezone=True), nullable=True)

    # 最后一次推送时的文件 SHA-256（用于本地比对是否已修改）
    cloud_hash = Column(String(64), nullable=True)

    # 关系
    tags = relationship("Tag", secondary=document_tag, back_populates="documents", lazy="selectin")
    versions = relationship("Version", back_populates="document", cascade="all, delete-orphan",
                            order_by="Version.version_number.desc()",
                            foreign_keys="Version.document_id")


class Version(Base):
    """
    版本表 —— 文档的某一个版本
    单文件文档：每个版本对应一个完整文件
    文件夹文档：每个版本对应文件夹内某个文件的某一版
    """
    __tablename__ = "versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)

    # 所属文档
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)

    # 版本号（1, 2, 3...）
    version_number = Column(Integer, nullable=False)

    # 该版本文件的物理路径（绝对路径）
    file_path = Column(String(1000), nullable=False)

    # 文件夹内的相对路径（文件夹文档专用）
    # 单文件文档为 NULL
    # 文件夹文档为如 "04-合同/采购合同.pdf"
    relative_path = Column(String(500), nullable=True)

    # SHA-256 文件哈希
    sha256_hash = Column(String(64), nullable=True, index=True)

    # 文件夹整体快照的归档摘要(M1 新增,契约 §1;单文件为 NULL)
    manifest_hash = Column(String(64), nullable=True)

    # 文件大小（字节）
    file_size = Column(BigInteger, default=0, nullable=False)

    # 原始文件名
    original_filename = Column(String(255), nullable=False)

    # 版本备注（可选）
    note = Column(Text, nullable=True)

    # 是否为当前版本
    is_current = Column(Boolean, default=True, nullable=False)

    # 时间戳
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # 同步状态
    sync_status = Column(String(10), nullable=False, default="local")

    # 最后同步时间（UTC）
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    # 上次记录的文件修改时间（os.stat().st_mtime），用于 scan-changes 的 mtime 预检
    # 与当前 mtime 相同则跳过 SHA-256 计算，大幅降低 I/O
    last_file_mtime = Column(Float, nullable=True)

    # 关系
    document = relationship("Document", back_populates="versions",
                            foreign_keys="Version.document_id")

    # (document_id, version_number) 唯一(M1-R01,契约 §2.2):空库由 create_all 建立,
    # 旧库由迁移在清理重复号后建立同名唯一索引
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_versions_doc_num"),
    )


class ChangeLog(Base):
    """
    变更日志表 —— 记录每次 CRUD 操作，为增量云端同步提供基础
    每条记录对应一次原子操作（create / update / delete / trash / restore 等）
    """
    __tablename__ = "change_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    device_id = Column(String(36), nullable=False, index=True)          # 发生变更的设备
    entity_type = Column(String(20), nullable=False, index=True)        # document / version / tag
    entity_id = Column(String(36), nullable=False, index=True)          # 对应实体的 UUID
    operation = Column(String(20), nullable=False)                       # create/update/delete/trash/restore/tag_add/tag_remove
    payload = Column(Text, nullable=True)                                # JSON 快照（变更后的关键字段）
    changed_at = Column(DateTime(timezone=True), nullable=False,
                        default=utc_now, index=True)

    # 推送时间戳：NULL=待推送，非NULL=已推送
    pushed_at = Column(DateTime(timezone=True), nullable=True, index=True)


class Tag(Base):
    """
    标签表
    """
    __tablename__ = "tags"

    id = Column(String(36), primary_key=True, default=generate_uuid)

    # 标签名称（唯一）
    name = Column(String(100), nullable=False, unique=True, index=True)

    # 标签颜色（hex 值，如 #FF5733，可选）
    color = Column(String(7), nullable=True)

    # 关系
    documents = relationship("Document", secondary=document_tag, back_populates="tags", lazy="selectin")


class ShareLink(Base):
    """
    个人资料 ↔ 共享资料关联(M1 新增,契约 §2.4)
    本地一份资料最多关联一个共享资料。
    """
    __tablename__ = "share_links"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    local_document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'),
                               nullable=False, unique=True, index=True)
    server_url = Column(String(255), nullable=False)
    server_id = Column(String(36), nullable=True, index=True)
    account_id = Column(String(36), nullable=True)
    workspace_id = Column(String(36), nullable=False)
    shared_document_id = Column(String(36), nullable=False)
    base_version_id = Column(String(36), nullable=True)
    observed_version_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class Favorite(Base):
    """
    收藏(M1 新增,契约 §2.5)
    收藏资料引用而非固定工作路径;共享目标仅存引用,不复制内容。
    """
    __tablename__ = "favorites"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # local = 个人资料 / shared = 共享资料
    target_type = Column(String(10), nullable=False)
    local_document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'),
                               nullable=True, index=True)
    server_url = Column(String(255), nullable=True)
    server_id = Column(String(36), nullable=True, index=True)
    workspace_id = Column(String(36), nullable=True)
    shared_document_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class VersionSource(Base):
    """Successful remote provenance; deleting a local version never erases a submit base."""
    __tablename__ = "version_sources"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    local_document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    # Keep the historical identifier after a version is deliberately removed.
    local_version_id = Column(String(36), nullable=False, index=True)
    server_id = Column(String(36), nullable=False)
    account_id = Column(String(36), nullable=False)
    workspace_id = Column(String(36), nullable=False)
    source_type = Column(String(20), nullable=False)
    shared_document_id = Column(String(36), nullable=True)
    shared_version_id = Column(String(36), nullable=True)
    delivery_id = Column(String(36), nullable=True)
    item_id = Column(String(36), nullable=True)
    operation_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class RemoteOperation(Base):
    """Durable request identity and recovery state, containing no credentials."""
    __tablename__ = "remote_operations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    server_id = Column(String(36), nullable=False)
    account_id = Column(String(36), nullable=False)
    workspace_id = Column(String(36), nullable=False)
    request_id = Column(String(128), nullable=False)
    kind = Column(String(30), nullable=False)
    local_document_id = Column(String(36), nullable=True, index=True)
    input_json = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default='pending', index=True)
    result_json = Column(Text, nullable=True)
    recovery_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    __table_args__ = (
        UniqueConstraint('server_id', 'account_id', 'workspace_id', 'request_id', name='uq_remote_request'),
    )
