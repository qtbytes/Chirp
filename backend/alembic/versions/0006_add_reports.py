"""add reports table

A report is a row ``(reporter_id, post_id, reason, details, created_at)`` -- a
moderation signal, not a personal filter. One report per (reporter, post) pair,
enforced by a unique constraint; an index on ``post_id`` serves the moderator's
"everything reported about this post" read.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('details', sa.String(length=280), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['reporter_id'], ['users.id']),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'reporter_id', 'post_id', name='uq_report_reporter_post'
        ),
    )
    op.create_index('ix_reports_post', 'reports', ['post_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reports_post', table_name='reports')
    op.drop_table('reports')
