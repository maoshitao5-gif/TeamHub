"""本机可重建派生索引；版本及人工元数据仍由原表保存。"""
from sqlalchemy import Column, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from backend.app.database import Base


class DocumentIndex(Base):
    __tablename__ = 'document_indexes'

    document_id = Column(String(36), ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    version_id = Column(String(36), ForeignKey('versions.id', ondelete='CASCADE'), nullable=True)
    content_hash = Column(String(64), nullable=True)
    parser_version = Column(String(30), nullable=False)
    generation = Column(Integer, nullable=False, default=1)
    state = Column(String(20), nullable=False, default='queued', index=True)
    result_json = Column(Text, nullable=True)
    ai_json = Column(Text, nullable=True)
    ai_metadata_hash = Column(String(64), nullable=True)
    ai_metadata_json = Column(Text, nullable=True)


class IndexText(Base):
    __tablename__ = 'index_texts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(36), ForeignKey('document_indexes.document_id', ondelete='CASCADE'),
                         nullable=False, index=True)
    kind = Column(String(10), nullable=False)
    body = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint('document_id', 'kind', name='uq_index_text_document_kind'),)


class IndexChunk(Base):
    """已发布的原文单位；位置和快照身份随正文一起保存。"""
    __tablename__ = 'index_chunks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(36), ForeignKey('document_indexes.document_id', ondelete='CASCADE'),
                         nullable=False)
    version_id = Column(String(36), ForeignKey('versions.id', ondelete='CASCADE'), nullable=False)
    generation = Column(Integer, nullable=False)
    unit_id = Column(String(80), nullable=False)
    ordinal = Column(Integer, nullable=False)
    kind = Column(String(10), nullable=False)
    body = Column(Text, nullable=False)
    context = Column(Text, nullable=False)
    label = Column(Text, nullable=False)
    location_json = Column(Text, nullable=False)
    search_segments_json = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False)
    source_hash = Column(String(64), nullable=False)
    parser_version = Column(String(30), nullable=False)

    __table_args__ = (
        UniqueConstraint('document_id', 'version_id', 'generation', 'unit_id',
                         name='uq_index_chunk_source_unit'),
        Index('ix_index_chunks_current', 'document_id', 'version_id', 'generation', 'ordinal'),
    )


class IndexWorkItem(Base):
    """解析完成后保存产物与发布游标，崩溃只重做尚未提交的批次。"""
    __tablename__ = 'index_work_items'

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(36), ForeignKey('document_indexes.document_id', ondelete='CASCADE'),
                         nullable=False)
    version_id = Column(String(36), ForeignKey('versions.id', ondelete='CASCADE'), nullable=True)
    generation = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=True)
    parser_version = Column(String(30), nullable=False)
    stage = Column(String(20), nullable=False, default='parse')
    state = Column(String(20), nullable=False, default='queued', index=True)
    attempt = Column(Integer, nullable=False, default=0)
    cursor = Column(Integer, nullable=False, default=0)
    total_units = Column(Integer, nullable=False, default=0)
    lease_until = Column(Float, nullable=True)
    lease_token = Column(String(36), nullable=True)
    parsed_result_json = Column(Text, nullable=True)

    __table_args__ = (UniqueConstraint('document_id', 'generation', name='uq_index_work_generation'),)
