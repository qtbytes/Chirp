"""user email and pending_email

Both columns are nullable. Accounts that predate them keep NULL for each: they
can still log in, and simply cannot reset a password until they add an address.

``email`` (the confirmed address) is uniquely indexed; ``pending_email`` (a mere
claim) is not. SQLite, like every backend here, allows any number of NULLs in a
unique index, so the existing rows do not collide with one another.

Revision ID: 0003
Revises: 0001
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=254), nullable=True))
        batch_op.add_column(sa.Column('pending_email', sa.String(length=254), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))
        batch_op.drop_column('pending_email')
        batch_op.drop_column('email')
