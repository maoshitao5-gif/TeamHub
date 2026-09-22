"""初始数据库迁移

Revision ID: 001
Revises:
Create Date: 2026-02-28

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 所有表由 Base.metadata.create_all 在 init_db() 时自动创建
    # 此处仅作占位迁移记录
    pass


def downgrade() -> None:
    pass
