"""
数据模型模块
定义数据库表结构和关系
"""
from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Table, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

# FileTag 中间表，用于实现文件与标签的多对多关系
file_tag_association = Table(
    'file_tag',
    Base.metadata,
    Column('file_id', Integer, ForeignKey('files.id'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tags.id'), primary_key=True)
)


class File(Base):
    """
    文件表模型
    存储文件的基本信息和元数据
    """
    __tablename__ = "files"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True, comment="文件ID")

    # 原始文件名（用户上传时的文件名）
    original_filename = Column(String(255), nullable=False, comment="原始文件名")

    # 文件在服务器上的存储路径（相对于 storage/ 目录）
    storage_path = Column(String(500), nullable=False, unique=True, comment="存储路径")

    # SHA-256 哈希值，用于文件查重
    sha256_hash = Column(String(64), nullable=False, unique=True, index=True, comment="SHA-256哈希值")

    # 文件大小（字节）
    file_size = Column(BigInteger, nullable=False, comment="文件大小（字节）")

    # 上传时间
    upload_time = Column(DateTime, default=datetime.utcnow, nullable=False, comment="上传时间")

    # 相对路径（用于文件夹上传，存储文件在文件夹中的相对路径）
    relative_path = Column(String(500), nullable=True, comment="相对路径（文件夹结构）")

    # 多对多关系：一个文件可以有多个标签
    tags = relationship("Tag", secondary=file_tag_association, back_populates="files")


class Tag(Base):
    """
    标签表模型
    存储标签信息
    """
    __tablename__ = "tags"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True, comment="标签ID")

    # 标签名称（唯一，不区分大小写）
    name = Column(String(50), nullable=False, unique=True, index=True, comment="标签名称")

    # 多对多关系：一个标签可以关联多个文件
    files = relationship("File", secondary=file_tag_association, back_populates="tags")


class User(Base):
    """
    用户表模型
    存储用户账号和密码信息
    """
    __tablename__ = "users"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True, comment="用户ID")

    # 用户名（唯一）
    username = Column(String(50), nullable=False, unique=True, index=True, comment="用户名")

    # 密码哈希值（不存储明文密码）
    hashed_password = Column(String(255), nullable=False, comment="密码哈希值")

    # 创建时间
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")

    # 最后登录时间
    last_login = Column(DateTime, nullable=True, comment="最后登录时间")
    
    # 是否为超级管理员
    is_admin = Column(Boolean, default=False, nullable=False, comment="是否为超级管理员")
