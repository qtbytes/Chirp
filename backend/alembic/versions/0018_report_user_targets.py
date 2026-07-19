"""report user targets

A report's target becomes either a post or an account: ``post_id`` turns
nullable and ``reported_user_id`` arrives beside it, with a check constraint
holding every row to exactly one of the two. User reports flag conduct no
single post captures (a profile, a DM pattern). The new unique constraint
mirrors ``uq_report_reporter_post`` for the user kind -- NULLs are distinct, so
each constraint only bites for its own target kind -- and the status index
mirrors the queue's per-post read.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0018'
down_revision: Union[str, Sequence[str], None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reported_user_id', sa.Integer(), nullable=True))
        batch_op.alter_column('post_id', existing_type=sa.Integer(), nullable=True)
        batch_op.create_check_constraint(
            'ck_report_exactly_one_target',
            '(post_id IS NULL) != (reported_user_id IS NULL)',
        )
        batch_op.create_unique_constraint(
            'uq_report_reporter_user', ['reporter_id', 'reported_user_id']
        )
        batch_op.create_index(
            'ix_reports_status_user', ['status', 'reported_user_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_reports_reported_user_users', 'users', ['reported_user_id'], ['id']
        )


def downgrade() -> None:
    # Destructive for user reports: post_id cannot go back to NOT NULL while
    # user-target rows exist, so they are dropped.
    op.execute("DELETE FROM reports WHERE post_id IS NULL")
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_constraint('fk_reports_reported_user_users', type_='foreignkey')
        batch_op.drop_index('ix_reports_status_user')
        batch_op.drop_constraint('uq_report_reporter_user', type_='unique')
        batch_op.drop_constraint('ck_report_exactly_one_target', type_='check')
        batch_op.alter_column('post_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('reported_user_id')
