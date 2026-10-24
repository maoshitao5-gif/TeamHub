"""Document ACL, independent delivery references and persistent service identity."""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cloud_documents', sa.Column('visibility', sa.String(20),
                                               sa.CheckConstraint("visibility IN ('workspace', 'restricted')", name='ck_document_visibility'), nullable=False,
                                               server_default='workspace'))
    op.add_column('cloud_documents', sa.Column('access_revision', sa.Integer(), nullable=False,
                                               server_default='1'))
    op.create_table('document_access',
        sa.Column('document_id', sa.String(36), sa.ForeignKey('cloud_documents.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('granted_by', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('reader', 'contributor', 'manager')", name='ck_document_access_role'))
    # Existing documents remain workspace-visible. Only their known creator gains an explicit grant.
    op.execute(sa.text("INSERT INTO document_access (document_id,user_id,role,granted_by,created_at) "
        "SELECT d.id,u.id,'manager',u.id,CURRENT_TIMESTAMP FROM cloud_documents d "
        "JOIN users u ON u.id=d.created_by"))
    op.create_table('server_metadata',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('server_id', sa.String(36), nullable=False, unique=True),
        sa.Column('protocol_major', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_server_metadata_singleton'))
    metadata = sa.table('server_metadata', sa.column('id'), sa.column('server_id'),
                        sa.column('protocol_major'), sa.column('schema_version'), sa.column('created_at'))
    op.bulk_insert(metadata, [dict(id=1, server_id=str(uuid.uuid4()), protocol_major=1,
                                 schema_version='005', created_at=datetime.now(timezone.utc))])
    op.create_table('deliveries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(36), sa.ForeignKey('teams.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspaces.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('sender_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('request_id', sa.String(128), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('note', sa.Text()), sa.Column('status', sa.String(20), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recalled_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('sender_id', 'workspace_id', 'request_id', name='uq_delivery_sender_ws_request'),
        sa.CheckConstraint("status IN ('sent', 'recalled')", name='ck_delivery_status'))
    for column in ('team_id', 'workspace_id', 'sender_id'):
        op.create_index('ix_deliveries_' + column, 'deliveries', [column])
    op.create_table('delivery_recipients',
        sa.Column('delivery_id', sa.String(36), sa.ForeignKey('deliveries.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('recipient_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('read_at', sa.DateTime(timezone=True)))
    op.create_table('delivery_items',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('delivery_id', sa.String(36), sa.ForeignKey('deliveries.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('doc_type', sa.String(10), nullable=False),
        sa.Column('storage_key', sa.String(1000), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('manifest_hash', sa.String(64)),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('file_count', sa.Integer(), nullable=False),
        sa.Column('total_size', sa.BigInteger(), nullable=False),
        sa.Column('source_document_id', sa.String(36)),
        sa.Column('source_version_id', sa.String(36)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for column in ('delivery_id', 'content_hash'):
        op.create_index('ix_delivery_items_' + column, 'delivery_items', [column])


def downgrade():
    connection = op.get_bind()
    if (connection.execute(sa.text("SELECT 1 FROM cloud_documents WHERE visibility='restricted' LIMIT 1")).first()
            or connection.execute(sa.text('SELECT 1 FROM deliveries LIMIT 1')).first()):
        raise RuntimeError('降级已停止：指定资料或投递记录仍存在，不能移除其授权与留存边界')
    for table in ('delivery_items', 'delivery_recipients', 'deliveries', 'server_metadata', 'document_access'):
        op.drop_table(table)
    op.drop_column('cloud_documents', 'access_revision')
    op.drop_column('cloud_documents', 'visibility')
