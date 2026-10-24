"""M1 契约迁移:资料/版本/标签新字段、唯一约束、发布请求与管理操作表

Revision ID: 004
Revises: 003
Create Date: 2026-09-23

依据 docs/MVP数据与接口契约.md §3/§6.2:
- cloud_documents:+doc_type / current_version_id / created_by / trashed_by
- cloud_versions:+content_hash / manifest_hash / storage_key / author_id / base_version_id,
  UNIQUE(document_id, version_number)
- cloud_document_tags:+added_by / created_at
- 新表 publication_requests / admin_actions
- 数据回填:is_folder→doc_type;uploader_id→created_by;sha256_hash→content_hash;
  oss_key→storage_key;is_current→current_version_id(缺失取最大版本号);
  状态值保持不变

SQLite 约束说明:FK 与唯一约束在 ALTER 中无法补加,唯一约束用 CREATE UNIQUE INDEX 实现;
旧列保留(过渡),删除留待 M4 重写旧写入路径之后。
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _check_legacy_versions(conn) -> None:
    issues = []
    for label, query in (
        ("重复版本号", "SELECT document_id FROM cloud_versions GROUP BY document_id, version_number HAVING COUNT(*) > 1"),
        ("多个当前版本", "SELECT document_id FROM cloud_versions WHERE is_current=1 GROUP BY document_id HAVING COUNT(*) > 1"),
        ("旧子文件历史", "SELECT DISTINCT document_id FROM cloud_versions WHERE relative_path IS NOT NULL AND relative_path != ''"),
    ):
        issues.extend(f"{label}: {row[0]}" for row in conn.execute(sa.text(query)))
    if issues:
        raise RuntimeError("迁移已停止，未自动重编号或选择历史。请在副本中核实并处理后重试：" + "; ".join(issues))


def upgrade() -> None:
    _check_legacy_versions(op.get_bind())
    # ── cloud_documents 新列 ─────────────────────────────────
    op.add_column('cloud_documents', sa.Column('doc_type', sa.String(10), nullable=False, server_default='file'))
    op.add_column('cloud_documents', sa.Column('current_version_id', sa.String(36), nullable=True))
    op.add_column('cloud_documents', sa.Column('created_by', sa.String(36), nullable=True))
    op.add_column('cloud_documents', sa.Column('trashed_by', sa.String(36), nullable=True))
    op.create_index('ix_cloud_documents_created_by', 'cloud_documents', ['created_by'])

    # ── cloud_versions 新列 + 唯一约束 ───────────────────────
    op.add_column('cloud_versions', sa.Column('content_hash', sa.String(64), nullable=True))
    op.add_column('cloud_versions', sa.Column('manifest_hash', sa.String(64), nullable=True))
    op.add_column('cloud_versions', sa.Column('storage_key', sa.String(1000), nullable=True))
    op.add_column('cloud_versions', sa.Column('author_id', sa.String(36), nullable=True))
    op.add_column('cloud_versions', sa.Column('base_version_id', sa.String(36), nullable=True))
    op.create_index('ix_cloud_versions_content_hash', 'cloud_versions', ['content_hash'])
    op.create_index('ix_cloud_versions_author_id', 'cloud_versions', ['author_id'])
    # 歧义旧数据已在任何 DDL 前拒绝
    op.create_index(
        'uq_cloud_version_doc_num', 'cloud_versions',
        ['document_id', 'version_number'], unique=True,
    )

    # ── cloud_document_tags 贡献者列 ─────────────────────────
    op.add_column('cloud_document_tags', sa.Column('added_by', sa.String(36), nullable=True))
    op.add_column('cloud_document_tags', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))

    # ── 新表 ────────────────────────────────────────────────
    op.create_table(
        'publication_requests',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('actor_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('result_document_id', sa.String(36), nullable=True),
        sa.Column('result_version_id', sa.String(36), nullable=True),
        sa.Column('error_code', sa.String(50), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('actor_id', 'workspace_id', 'request_id', name='uq_pubreq_actor_ws_req'),
    )
    op.create_index('ix_publication_requests_actor_id', 'publication_requests', ['actor_id'])
    op.create_index('ix_publication_requests_workspace_id', 'publication_requests', ['workspace_id'])

    op.create_table(
        'admin_actions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('actor_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('target', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_admin_actions_actor_id', 'admin_actions', ['actor_id'])

    # ── 数据回填(幂等:只处理未迁移的行) ──────────────────────
    op.execute("UPDATE cloud_documents SET doc_type='folder' WHERE is_folder=1 AND doc_type='file'")
    op.execute("UPDATE cloud_documents SET created_by=uploader_id WHERE created_by IS NULL AND uploader_id IS NOT NULL")
    # status 更名(organized→active)已撤销:MVP 期间保留旧状态值,避免破坏现有过滤
    op.execute("UPDATE cloud_versions SET content_hash=sha256_hash WHERE content_hash IS NULL AND sha256_hash IS NOT NULL")
    op.execute("UPDATE cloud_versions SET storage_key=oss_key WHERE storage_key IS NULL AND oss_key IS NOT NULL")
    # 已通过旧数据预检，不猜测多条当前标记的含义
    # is_current=true → current_version_id
    op.execute("""
        UPDATE cloud_documents SET current_version_id = (
            SELECT v.id FROM cloud_versions v
            WHERE v.document_id = cloud_documents.id AND v.is_current = 1
        )
        WHERE current_version_id IS NULL
          AND EXISTS (SELECT 1 FROM cloud_versions v
                      WHERE v.document_id = cloud_documents.id AND v.is_current = 1)
    """)
    # 无 is_current 标记时兜底取最大版本号
    op.execute("""
        UPDATE cloud_documents SET current_version_id = (
            SELECT v.id FROM cloud_versions v
            WHERE v.document_id = cloud_documents.id
            ORDER BY v.version_number DESC LIMIT 1
        )
        WHERE current_version_id IS NULL
          AND EXISTS (SELECT 1 FROM cloud_versions v WHERE v.document_id = cloud_documents.id)
    """)


def downgrade() -> None:
    op.drop_table('admin_actions')
    op.drop_table('publication_requests')
    op.drop_index('uq_cloud_version_doc_num', table_name='cloud_versions')
    op.drop_index('ix_cloud_versions_author_id', table_name='cloud_versions')
    op.drop_index('ix_cloud_versions_content_hash', table_name='cloud_versions')
    op.drop_index('ix_cloud_documents_created_by', table_name='cloud_documents')
    for table, cols in {
        'cloud_document_tags': ['added_by', 'created_at'],
        'cloud_versions': ['content_hash', 'manifest_hash', 'storage_key', 'author_id', 'base_version_id'],
        'cloud_documents': ['doc_type', 'current_version_id', 'created_by', 'trashed_by'],
    }.items():
        for col in cols:
            op.drop_column(table, col)
