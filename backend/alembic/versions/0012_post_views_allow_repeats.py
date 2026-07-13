"""allow repeated post views

Rebuilds ``post_views`` without the ``(user_id, post_id)`` primary key.
Views are Twitter-style impressions: every render or click counts again,
so the table is now an append-only log with a surrogate ``id`` key.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot alter a primary key in place; the table is a dedup log
    # whose history is no longer meaningful, so drop and recreate it.
    op.drop_table('post_views')
    op.create_table(
        'post_views',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('post_id', sa.Integer(), sa.ForeignKey('posts.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_post_views_post_id', 'post_views', ['post_id'])


def downgrade() -> None:
    op.drop_index('ix_post_views_post_id', table_name='post_views')
    op.drop_table('post_views')
    op.create_table(
        'post_views',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('post_id', sa.Integer(), sa.ForeignKey('posts.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'post_id', name='pk_post_views'),
    )
