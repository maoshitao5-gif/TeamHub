"""添加 join_requests 表和 cloud_documents.uploader_id 字段

Revision ID: 003
Revises: 002
Create Date: 2026-05-20

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建 join_requests 表（唯一约束在 create_table 中声明，兼容 SQLite）
    op.create_table(
        'join_requests',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(36), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reviewed_by', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('team_id', 'user_id', 'status', name='uq_join_req_team_user_status'),
    )
    op.create_index('ix_join_requests_team_id', 'join_requests', ['team_id'])
    op.create_index('ix_join_requests_user_id', 'join_requests', ['user_id'])

    # cloud_documents 添加 uploader_id 字段（不声明 FK，SQLite 不支持 ALTER ADD CONSTRAINT）
    op.add_column(
        'cloud_documents',
        sa.Column('uploader_id', sa.String(36), nullable=True),
    )
    op.create_index('ix_cloud_documents_uploader_id', 'cloud_documents', ['uploader_id'])


def downgrade() -> None:
    op.drop_index('ix_cloud_documents_uploader_id', table_name='cloud_documents')
    op.drop_column('cloud_documents', 'uploader_id')
    op.drop_table('join_requests')
