"""
数据模型模块
定义 Document、Version、Tag 表结构和关系
使用 UUID v7 作为主键，为未来远端同步预留
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, BigInteger, Integer, Boolean, Table, ForeignKey, Text
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

    # 文件库中的存储路径（相对于文件库根目录）
    storage_path = Column(String(1000), nullable=True)

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
    trashed_at = Column(DateTime(timezone=True), nullable=True)
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)

    # 关系
    tags = relationship("Tag", secondary=document_tag, back_populates="documents", lazy="selectin")
    versions = relationship("Version", back_populates="document", cascade="all, delete-orphan",
                            order_by="Version.version_number.desc()")


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

    # 关系
    document = relationship("Document", back_populates="versions")


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
