"""add post search: FTS5 index + hashtag/mention tables

Adds full-text search over post content and the structured entity tables the
write-time extraction populates:

- ``posts_fts``: a SQLite FTS5 external-content virtual table over
  ``posts.content``, kept in sync by AFTER INSERT/UPDATE/DELETE triggers and
  backfilled from the existing rows. The DDL lives in ``app.db.fts`` so the same
  statements build the index here and under ``create_all()`` in the tests.
- ``post_hashtags`` / ``post_mentions``: one row per ``#tag`` / resolved
  ``@mention`` on a post.

The FTS block is SQLite-only; on any other backend the virtual table is skipped
(the app targets SQLite -- see the WAL pragmas in app/db/database.py).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.fts import SQLITE_FTS_CREATE, SQLITE_FTS_DROP


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'post_hashtags',
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('tag', sa.String(length=140), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id']),
        sa.PrimaryKeyConstraint('post_id', 'tag', name='pk_post_hashtags'),
    )
    op.create_index('ix_post_hashtags_tag', 'post_hashtags', ['tag'], unique=False)

    op.create_table(
        'post_mentions',
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('mentioned_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id']),
        sa.ForeignKeyConstraint(['mentioned_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint(
            'post_id', 'mentioned_user_id', name='pk_post_mentions'
        ),
    )
    op.create_index(
        'ix_post_mentions_user', 'post_mentions', ['mentioned_user_id'], unique=False
    )

    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        for statement in SQLITE_FTS_CREATE:
            op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        for statement in SQLITE_FTS_DROP:
            op.execute(statement)

    op.drop_index('ix_post_mentions_user', table_name='post_mentions')
    op.drop_table('post_mentions')
    op.drop_index('ix_post_hashtags_tag', table_name='post_hashtags')
    op.drop_table('post_hashtags')
