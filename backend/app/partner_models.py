"""个人库内的伙伴会话；私有结果可保存有界原生历史，不保存密钥或图片像素。"""
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint

from backend.app.database import Base
from backend.app.models import generate_uuid, utc_now


class PartnerSession(Base):
    __tablename__ = 'partner_sessions'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String(120), nullable=False, default='新对话')
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class PartnerTask(Base):
    __tablename__ = 'partner_tasks'
    __table_args__ = (UniqueConstraint('session_id', 'request_id', name='uq_partner_task_request'),)
    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey('partner_sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    request_id = Column(String(100), nullable=False)
    preparation_id = Column(String(100), nullable=False)
    state = Column(String(20), nullable=False, default='queued', index=True)
    message = Column(Text, nullable=False)
    input_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False, default='{}')
    error_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class PartnerMessage(Base):
    __tablename__ = 'partner_messages'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey('partner_sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    task_id = Column(String(36), ForeignKey('partner_tasks.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(String(12), nullable=False)
    content = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=False, default='[]')
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
