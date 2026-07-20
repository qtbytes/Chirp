"""pinned post on profile

Adds ``users.pinned_post_id``: one of the user's own top-level tweets, shown at
the top of their profile (Twitter-style). NULL means nothing is pinned. No
ON DELETE behaviour is declared -- SQLite here runs with foreign keys off, so
``post_repository.delete_post`` nulls the column by hand when a pinned post is
deleted.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(
            sa.Column('pinned_post_id', sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('pinned_post_id')
