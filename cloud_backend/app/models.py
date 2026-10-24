"""
云服务数据模型
多租户架构：User → Team → Workspace → Document/Version/Tag
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, DateTime, BigInteger, Integer, Boolean,
    Text, ForeignKey, UniqueConstraint, CheckConstraint, JSON
)
from sqlalchemy.orm import relationship
from app.database import Base


def _gen_uuid() -> str:
    """生成 UUID v4 字符串"""
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ========== 用户与认证 ==========

class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    email = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)

    # 关系
    team_memberships = relationship("TeamMember", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    """刷新令牌表（SHA-256 哈希存储，支持多设备）"""
    __tablename__ = "refresh_tokens"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)  # SHA-256 hex
    device_id = Column(String(36), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    user = relationship("User", back_populates="refresh_tokens")


# ========== 团队与工作空间 ==========

class Team(Base):
    """团队表"""
    __tablename__ = "teams"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), nullable=False, unique=True, index=True)  # URL 友好唯一标识
    storage_quota = Column(BigInteger, default=10 * 1024 ** 3, nullable=False)  # 默认 10GB
    storage_used = Column(BigInteger, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)

    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    workspaces = relationship("Workspace", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    """团队成员关联表（含角色）"""
    __tablename__ = "team_members"

    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # 角色：owner（拥有者）/ admin（管理员）/ member（普通成员）/ viewer（只读）
    role = Column(String(20), nullable=False, default="member")
    joined_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    team = relationship("Team", back_populates="members")
    user = relationship("User", back_populates="team_memberships")


class JoinRequest(Base):
    """加入团队申请表"""
    __tablename__ = "join_requests"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending")  # pending / approved / rejected
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    reviewed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    team = relationship("Team", backref="join_requests")
    user = relationship("User", foreign_keys=[user_id], backref="join_requests")
    reviewer = relationship("User", foreign_keys=[reviewed_by])

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", "status", name="uq_join_req_team_user_status"),
    )


class Workspace(Base):
    """工作空间表（OSS 隔离：{prefix}/{team_id}/{ws_id}/）"""
    __tablename__ = "workspaces"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)

    team = relationship("Team", back_populates="workspaces")
    documents = relationship("CloudDocument", back_populates="workspace", cascade="all, delete-orphan")
    tags = relationship("CloudTag", back_populates="workspace", cascade="all, delete-orphan")
    change_logs = relationship("CloudChangeLog", back_populates="workspace", cascade="all, delete-orphan")


# ========== 文档与版本 ==========

class CloudDocument(Base):
    """
    云端文档表(M1 契约 §3.1)
    id 由服务端生成;当前版本以 current_version_id 为唯一事实来源(新代码)。
    过渡期保留旧列(is_folder/uploader_id/server_version/is_current 等),
    旧写入路径在 M4 由发布通路整体替换后删除。
    """
    __tablename__ = "cloud_documents"

    id = Column(String(36), primary_key=True, default=_gen_uuid)   # M1:服务端生成
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_folder = Column(Boolean, default=False, nullable=False)     # 废弃过渡:新代码用 doc_type
    doc_type = Column(String(10), nullable=False, default="file")  # file / folder / archive
    status = Column(String(20), nullable=False, default="pending")  # pending/organized/trashed(保留旧值,见契约 §6.2)
    file_count = Column(Integer, default=1, nullable=False)
    total_size = Column(BigInteger, default=0, nullable=False)
    server_version = Column(Integer, default=1, nullable=False)     # 废弃过渡:冲突检测改用基准版本 ID
    current_version_id = Column(String(36), nullable=True)          # M1 新增:唯一当前版本来源
    uploader_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)  # 废弃过渡:用 created_by
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)   # M1 新增:发布者记录
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)
    trashed_at = Column(DateTime(timezone=True), nullable=True)
    trashed_by = Column(String(36), ForeignKey("users.id"), nullable=True)  # M1 新增:管理员操作留痕
    visibility = Column(String(20), nullable=False, default="workspace", server_default="workspace")
    access_revision = Column(Integer, nullable=False, default=1, server_default="1")

    workspace = relationship("Workspace", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploader_id])
    versions = relationship("CloudVersion", back_populates="document", cascade="all, delete-orphan",
                            order_by="CloudVersion.version_number.desc()")
    tags = relationship("CloudTag", secondary="cloud_document_tags", back_populates="documents")
    access_entries = relationship("DocumentAccess", cascade="all, delete-orphan")

    __table_args__ = (CheckConstraint("visibility IN ('workspace', 'restricted')", name="ck_document_visibility"),)


class DocumentAccess(Base):
    __tablename__ = "document_access"
    document_id = Column(String(36), ForeignKey("cloud_documents.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    role = Column(String(20), nullable=False, default="reader")
    granted_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    __table_args__ = (CheckConstraint("role IN ('reader', 'contributor', 'manager')", name="ck_document_access_role"),)


class CloudVersion(Base):
    """
    云端版本表(M1 契约 §3.2)
    (document_id, version_number) 唯一;content_hash 不设唯一约束。
    过渡期保留旧列(sha256_hash/oss_key/is_current/relative_path)。
    """
    __tablename__ = "cloud_versions"

    id = Column(String(36), primary_key=True, default=_gen_uuid)   # M1:服务端生成
    document_id = Column(String(36), ForeignKey("cloud_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    sha256_hash = Column(String(64), nullable=True, index=True)    # 废弃过渡:新代码用 content_hash
    content_hash = Column(String(64), nullable=True, index=True)   # M1 新增:内容摘要
    manifest_hash = Column(String(64), nullable=True)              # M1 新增:文件夹归档摘要
    oss_key = Column(String(1000), nullable=True)                  # 废弃过渡:新代码用 storage_key
    storage_key = Column(String(1000), nullable=True)              # M1 新增:不可变存储键
    file_size = Column(BigInteger, default=0, nullable=False)
    original_filename = Column(String(255), nullable=False)
    relative_path = Column(String(500), nullable=True)             # 废弃:文件夹为整体快照
    note = Column(Text, nullable=True)
    is_current = Column(Boolean, default=True, nullable=False)     # 废弃过渡:读方用 documents.current_version_id
    author_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)      # M1 新增:提交者
    base_version_id = Column(String(36), nullable=True)            # M1 新增:提交时声明的基准版本
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    document = relationship("CloudDocument", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_cloud_version_doc_num"),
    )


# ========== 标签 ==========

from sqlalchemy import Table

cloud_document_tags = Table(
    "cloud_document_tags",
    Base.metadata,
    Column("document_id", String(36), ForeignKey("cloud_documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("cloud_tags.id", ondelete="CASCADE"), primary_key=True),
    # M1 新增(契约 §3.3):贡献者记录,按关联行判断"本人贡献"
    Column("added_by", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=True),
)


class CloudTag(Base):
    """云端标签（工作空间内唯一）"""
    __tablename__ = "cloud_tags"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    color = Column(String(7), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    workspace = relationship("Workspace", back_populates="tags")
    documents = relationship("CloudDocument", secondary="cloud_document_tags", back_populates="tags")

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_cloud_tag_ws_name"),
    )


# ========== 设备与变更日志 ==========

class Device(Base):
    """设备注册表（每个用户可注册多台设备）"""
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(String(36), nullable=False)         # 设备端本地生成的唯一 ID
    device_name = Column(String(100), nullable=True)
    platform = Column(String(50), nullable=True)           # windows / macos / linux
    sync_cursors = Column(JSON, nullable=True)             # {workspace_id: last_sequence_number}
    last_seen_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    user = relationship("User", back_populates="devices")

    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uq_device_user_device"),
    )


class CloudChangeLog(Base):
    """
    云端变更日志
    sequence_number 是工作空间级别的单调递增游标
    设备只需 WHERE sequence_number > last_cursor 增量拉取
    """
    __tablename__ = "cloud_change_logs"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_number = Column(Integer, nullable=False, index=True)  # 工作空间内单调递增
    entity_type = Column(String(20), nullable=False)               # document / version / tag
    entity_id = Column(String(36), nullable=False)
    operation = Column(String(20), nullable=False)                 # create/update/delete/trash/restore
    payload = Column(JSON, nullable=True)                          # 变更后的关键字段快照
    device_id = Column(String(36), nullable=True)                  # 发起变更的设备 ID
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    workspace = relationship("Workspace", back_populates="change_logs")


# ========== 本地数据库备份 ==========


class PublicationRequest(Base):
    """
    发布请求(M1 新增,契约 §3.4)
    (actor_id, workspace_id, request_id) 唯一,实现幂等发布/版本提交。
    """
    __tablename__ = "publication_requests"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    request_id = Column(String(36), nullable=False)          # 客户端幂等键
    actor_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    request_hash = Column(String(64), nullable=False)        # 请求内容摘要,防幂等键被换负载
    action = Column(String(20), nullable=False)              # publish / submit_version
    status = Column(String(20), nullable=False, default="pending")  # pending / succeeded / failed
    result_document_id = Column(String(36), nullable=True)
    result_version_id = Column(String(36), nullable=True)
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("actor_id", "workspace_id", "request_id", name="uq_pubreq_actor_ws_req"),
    )


class AdminAction(Base):
    """
    管理操作记录(M1 新增,契约 §3.5)
    不含令牌与文件正文。
    """
    __tablename__ = "admin_actions"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    actor_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(30), nullable=False)   # trash / restore / purge / deactivate_user / remove_tag_link
    target = Column(JSON, nullable=True)          # 目标描述(资料/版本/关联 ID)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)


class UserBackup(Base):
    """
    用户本地数据库备份记录
    用户可将 .teamhub/db.sqlite + config.json 打包上传，换电脑时一键恢复
    """
    __tablename__ = "user_backups"

    id          = Column(String(36), primary_key=True, default=_gen_uuid)
    user_id     = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id   = Column(String(36), nullable=False)
    device_name = Column(String(100), nullable=False, default="")
    filename    = Column(String(200), nullable=False)   # teamhub-backup-YYYYMMDD-HHMMSS.zip
    file_size   = Column(Integer, nullable=False, default=0)
    oss_key     = Column(String(500), nullable=False)
    created_at  = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    confirmed   = Column(Boolean, nullable=False, default=False)   # OSS 上传是否已确认

    user = relationship("User", backref="backups")


class ServerMetadata(Base):
    """Stable service identity, carried with backups of the same service."""
    __tablename__ = "server_metadata"
    id = Column(Integer, primary_key=True, default=1)
    server_id = Column(String(36), nullable=False, unique=True, default=_gen_uuid)
    protocol_major = Column(Integer, nullable=False, default=1)
    schema_version = Column(String(20), nullable=False, default="005")
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    __table_args__ = (CheckConstraint("id = 1", name="ck_server_metadata_singleton"),)


class Delivery(Base):
    __tablename__ = "deliveries"
    id = Column(String(36), primary_key=True, default=_gen_uuid)
    team_id = Column(String(36), ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True)
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True)
    sender_id = Column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    request_id = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="sent")
    sent_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    recalled_at = Column(DateTime(timezone=True), nullable=True)
    items = relationship("DeliveryItem", back_populates="delivery", cascade="all, delete-orphan")
    recipients = relationship("DeliveryRecipient", back_populates="delivery", cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("sender_id", "workspace_id", "request_id", name="uq_delivery_sender_ws_request"),
        CheckConstraint("status IN ('sent', 'recalled')", name="ck_delivery_status"),
    )


class DeliveryRecipient(Base):
    __tablename__ = "delivery_recipients"
    delivery_id = Column(String(36), ForeignKey("deliveries.id", ondelete="CASCADE"), primary_key=True)
    recipient_id = Column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    delivery = relationship("Delivery", back_populates="recipients")


class DeliveryItem(Base):
    """Independent immutable content reference; source IDs are provenance, not ownership."""
    __tablename__ = "delivery_items"
    id = Column(String(36), primary_key=True, default=_gen_uuid)
    delivery_id = Column(String(36), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    doc_type = Column(String(10), nullable=False, default="file")
    storage_key = Column(String(1000), nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    manifest_hash = Column(String(64), nullable=True)
    file_size = Column(BigInteger, nullable=False)
    file_count = Column(Integer, nullable=False, default=1)
    total_size = Column(BigInteger, nullable=False, default=0)
    source_document_id = Column(String(36), nullable=True)
    source_version_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    delivery = relationship("Delivery", back_populates="items")
