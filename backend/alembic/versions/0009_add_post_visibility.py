"""add post visibility

Adds ``posts.visibility`` -- the per-tweet audience (``public`` / ``followers`` /
``private``). Existing rows default to ``public`` via the server default, so the
column backfills without a data migration.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'posts',
        sa.Column(
            'visibility',
            sa.String(length=16),
            nullable=False,
            server_default='public',
        ),
    )


def downgrade() -> None:
    op.drop_column('posts', 'visibility')
