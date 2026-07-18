"""add direct messages

1:1 conversations only. ``dm_conversations`` stores the pair normalized as
(low, high) so each pair has one row, plus per-participant read markers and a
denormalized ``last_message_at`` for inbox ordering. ``dm_messages`` holds the
messages; ordering within a conversation keys on the monotonic id.

Also adds ``users.dm_policy`` -- who may open a conversation with the user:
'everyone' (default), 'following' (only people the user follows), or 'none'.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'dm_policy',
            sa.String(length=16),
            nullable=False,
            server_default='everyone',
        ),
    )

    op.create_table(
        'dm_conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_low_id', sa.Integer(), nullable=False),
        sa.Column('user_high_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('low_last_read_message_id', sa.Integer(), nullable=True),
        sa.Column('high_last_read_message_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_low_id'], ['users.id']),
        sa.ForeignKeyConstraint(['user_high_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_low_id', 'user_high_id', name='uq_dm_conversations_pair'
        ),
    )
    op.create_index(
        'ix_dm_conversations_low_last',
        'dm_conversations',
        ['user_low_id', 'last_message_at'],
    )
    op.create_index(
        'ix_dm_conversations_high_last',
        'dm_conversations',
        ['user_high_id', 'last_message_at'],
    )

    op.create_table(
        'dm_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['dm_conversations.id']),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_dm_messages_conversation', 'dm_messages', ['conversation_id', 'id']
    )


def downgrade() -> None:
    op.drop_index('ix_dm_messages_conversation', table_name='dm_messages')
    op.drop_table('dm_messages')
    op.drop_index('ix_dm_conversations_high_last', table_name='dm_conversations')
    op.drop_index('ix_dm_conversations_low_last', table_name='dm_conversations')
    op.drop_table('dm_conversations')
    op.drop_column('users', 'dm_policy')
