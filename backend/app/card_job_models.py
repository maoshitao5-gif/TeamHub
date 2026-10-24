"""资料卡生成收据及逐资料刷新授权；不保存密钥或原始文件。"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from backend.app.database import Base
from backend.app.models import generate_uuid, utc_now


class CardRefreshPolicy(Base):
    __tablename__ = 'card_refresh_policies'
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    service_revision = Column(String(100), nullable=False)
    connection_revision = Column(String(100), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class DocumentCardJob(Base):
    __tablename__ = 'document_card_jobs'
    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    id = Column(String(36), nullable=False, default=generate_uuid)
    generation = Column(Integer, nullable=False, default=0)
    state = Column(String(20), nullable=False, default='waiting')
    scope_json = Column(Text, nullable=False)
    service_revision = Column(String(100), nullable=False)
    connection_revision = Column(String(100), nullable=False)
    expected_card_revision = Column(Integer, nullable=False, default=0)
    automatic = Column(Boolean, nullable=False, default=False)
    error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class CardRequest(Base):
    __tablename__ = 'document_card_requests'
    id = Column(String(100), primary_key=True)
    input_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
