"""
云服务数据模型
多租户架构：User → Team → Workspace → Document/Version/Tag
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, DateTime, BigInteger, Integer, Boolean,
    Text, ForeignKey, UniqueConstraint, JSON
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
    云端文档表
    id 与桌面端 Document.id 保持相同 UUID，无需映射表
    server_version 单调递增用于冲突检测
    """
    __tablename__ = "cloud_documents"

    id = Column(String(36), primary_key=True)          # 与桌面端 UUID 保持一致
    workspace_id = Column(String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_folder = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending/organized/missing/trashed
    file_count = Column(Integer, default=1, nullable=False)
    total_size = Column(BigInteger, default=0, nullable=False)
    server_version = Column(Integer, default=1, nullable=False)     # 冲突检测游标
    uploader_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)  # 推送者
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)
    trashed_at = Column(DateTime(timezone=True), nullable=True)

    workspace = relationship("Workspace", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploader_id])
    versions = relationship("CloudVersion", back_populates="document", cascade="all, delete-orphan",
                            order_by="CloudVersion.version_number.desc()")
    tags = relationship("CloudTag", secondary="cloud_document_tags", back_populates="documents")


class CloudVersion(Base):
    """云端版本表"""
    __tablename__ = "cloud_versions"

    id = Column(String(36), primary_key=True)              # 与桌面端 UUID 保持一致
    document_id = Column(String(36), ForeignKey("cloud_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    sha256_hash = Column(String(64), nullable=True, index=True)
    oss_key = Column(String(1000), nullable=True)          # 文件在 OSS 中的 key
    file_size = Column(BigInteger, default=0, nullable=False)
    original_filename = Column(String(255), nullable=False)
    relative_path = Column(String(500), nullable=True)     # 文件夹文档专用
    note = Column(Text, nullable=True)
    is_current = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)

    document = relationship("CloudDocument", back_populates="versions")


# ========== 标签 ==========

from sqlalchemy import Table

cloud_document_tags = Table(
    "cloud_document_tags",
    Base.metadata,
    Column("document_id", String(36), ForeignKey("cloud_documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("cloud_tags.id", ondelete="CASCADE"), primary_key=True),
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
