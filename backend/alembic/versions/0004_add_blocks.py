"""add blocks table

A block is a row ``(blocker_id, blocked_id)`` with a creation time. The pair is
the primary key, so a block is naturally idempotent, and an index on
``blocked_id`` makes the reverse lookup ("who has blocked me") as cheap as the
forward one the PK already covers.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'blocks',
        sa.Column('blocker_id', sa.Integer(), nullable=False),
        sa.Column('blocked_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['blocker_id'], ['users.id']),
        sa.ForeignKeyConstraint(['blocked_id'], ['users.id']),
        sa.PrimaryKeyConstraint('blocker_id', 'blocked_id', name='pk_blocks'),
    )
    op.create_index('ix_blocks_blocked', 'blocks', ['blocked_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_blocks_blocked', table_name='blocks')
    op.drop_table('blocks')
