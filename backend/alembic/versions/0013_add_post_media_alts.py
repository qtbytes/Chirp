"""add post media alts

Adds ``posts.media_alts`` -- per-image alt text parallel to ``media_urls``
(same length, empty string meaning "no alt"). Nullable, so existing rows and
posts without media need no backfill.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('posts', sa.Column('media_alts', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('posts', 'media_alts')
