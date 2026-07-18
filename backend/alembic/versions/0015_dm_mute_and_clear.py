"""dm conversation mute and one-directional delete

Per-participant conversation state on ``dm_conversations``:

- ``low_muted`` / ``high_muted``: a muted chat stays but stops counting toward
  that participant's unread badge.
- ``low_cleared_before_id`` / ``high_cleared_before_id``: "deleted the
  conversation" watermark. Messages with id <= the marker are invisible to
  that participant only; the other side keeps the full history, and a later
  message revives the chat from that point on.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('dm_conversations') as batch_op:
        batch_op.add_column(
            sa.Column(
                'low_muted', sa.Boolean(), nullable=False, server_default='0'
            )
        )
        batch_op.add_column(
            sa.Column(
                'high_muted', sa.Boolean(), nullable=False, server_default='0'
            )
        )
        batch_op.add_column(
            sa.Column('low_cleared_before_id', sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('high_cleared_before_id', sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('dm_conversations') as batch_op:
        batch_op.drop_column('high_cleared_before_id')
        batch_op.drop_column('low_cleared_before_id')
        batch_op.drop_column('high_muted')
        batch_op.drop_column('low_muted')
