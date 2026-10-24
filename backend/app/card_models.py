"""按保存版本维护的资料理解，与用户补充独立保存。"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from backend.app.database import Base
from backend.app.models import utc_now


class DocumentCard(Base):
    __tablename__ = 'document_cards'

    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    version_id = Column(String(36), ForeignKey('versions.id', ondelete='CASCADE'), primary_key=True)
    content_hash = Column(String(64), nullable=False)
    parser_version = Column(String(30), nullable=False)
    skill_version = Column(String(30), nullable=False)
    schema_version = Column(Integer, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    content_json = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=False)
    coverage_json = Column(Text, nullable=False)
    search_text = Column(Text, nullable=False)
    model = Column(String(200), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DocumentCardAnnotation(Base):
    __tablename__ = 'document_card_annotations'

    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    # 删除来源版本不删除用户补充；NULL 会明确显示为需核对。
    source_version_id = Column(String(36), ForeignKey('versions.id', ondelete='SET NULL'), nullable=True)
    note = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
