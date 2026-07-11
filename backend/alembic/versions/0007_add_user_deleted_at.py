"""add users.deleted_at

Account deletion is a soft delete: the row survives so posts others replied to
or quoted keep an author, and ``deleted_at`` marks the tombstone. Nullable, so
every existing account is simply "not deleted".

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'deleted_at')
