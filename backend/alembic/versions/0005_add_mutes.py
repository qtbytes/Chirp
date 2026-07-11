"""add mutes table

A mute is a row ``(muter_id, muted_id)`` with a creation time. The pair is the
primary key, so a mute is naturally idempotent. Unlike blocks there is no
reverse index: a mute only ever affects the muter's own reads, so the sole
lookup ("who have I muted") is already covered by the leading PK column.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mutes',
        sa.Column('muter_id', sa.Integer(), nullable=False),
        sa.Column('muted_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['muter_id'], ['users.id']),
        sa.ForeignKeyConstraint(['muted_id'], ['users.id']),
        sa.PrimaryKeyConstraint('muter_id', 'muted_id', name='pk_mutes'),
    )


def downgrade() -> None:
    op.drop_table('mutes')
