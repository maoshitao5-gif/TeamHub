"""个人库 AI 预整理派生记录；不改变 Document 的生命周期或版本身份。"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from backend.app.database import Base
from backend.app.models import generate_uuid, utc_now


class PreorganizePolicy(Base):
    __tablename__ = 'preorganize_policy'
    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    service_revision = Column(String(64), nullable=True)
    connection_revision = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class PreorganizeProposal(Base):
    __tablename__ = 'preorganize_proposals'
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    id = Column(String(36), nullable=False, unique=True, default=generate_uuid)
    generation = Column(Integer, nullable=False, default=1)
    state = Column(String(20), nullable=False, default='waiting', index=True)
    phase = Column(String(20), nullable=True)
    scope_json = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=False)
    catalog_json = Column(Text, nullable=False, default='{}')
    service_revision = Column(String(64), nullable=False)
    connection_revision = Column(String(64), nullable=False)
    automatic = Column(Boolean, nullable=False, default=False)
    result_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class PreorganizeRequest(Base):
    """成功入队的请求收据保留，重试网络请求不得重新发起付费任务。"""
    __tablename__ = 'preorganize_requests'
    id = Column(String(100), primary_key=True)
    input_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class PreorganizeOperation(Base):
    __tablename__ = 'preorganize_operations'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    request_id = Column(String(100), nullable=False, unique=True)
    # 历史记录在资料被删除后仍可解释；不能借记录重建已删资料。
    document_id = Column(String(36), nullable=False, index=True)
    document_name = Column(String(255), nullable=False)
    input_json = Column(Text, nullable=False)
    before_json = Column(Text, nullable=False)
    after_json = Column(Text, nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    undone_at = Column(DateTime(timezone=True), nullable=True)
