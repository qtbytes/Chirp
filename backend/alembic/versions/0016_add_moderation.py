"""add moderation

- ``users.is_moderator`` gates the moderation queue; granted only by the
  operator (deploy/set_moderator.py), never through the API.
- ``posts.taken_down_at`` marks a moderator takedown. The row survives -- unlike
  an author delete -- so the action is reversible and reports keep a target;
  read paths hide the post and detail views tombstone it.
- ``reports.status`` / ``resolved_at`` / ``resolved_by`` record the judgement.
  Reports resolve per *post*: dismissing or taking down closes every open
  report about that post together.

Autogenerate against the dev database also proposed dropping the pre-refactor
legacy tables (``tweets``, ``comments``, ``retweets``, ...) that still linger
there; those drops were removed by hand -- a fresh database never had them, and
``deploy/prune_db.py`` already strips them from anything shipped.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-19

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
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('taken_down_at', sa.DateTime(timezone=True), nullable=True)
        )

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'status', sa.String(length=16), server_default='open', nullable=False
            )
        )
        batch_op.add_column(
            sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column('resolved_by', sa.Integer(), nullable=True))
        batch_op.create_index(
            'ix_reports_status_post', ['status', 'post_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_reports_resolved_by_users', 'users', ['resolved_by'], ['id']
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_moderator', sa.Boolean(), server_default='0', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_moderator')

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_constraint('fk_reports_resolved_by_users', type_='foreignkey')
        batch_op.drop_index('ix_reports_status_post')
        batch_op.drop_column('resolved_by')
        batch_op.drop_column('resolved_at')
        batch_op.drop_column('status')

    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_column('taken_down_at')
