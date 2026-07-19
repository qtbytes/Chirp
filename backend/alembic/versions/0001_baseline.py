"""baseline

The schema as the models define it, and the point at which this project adopted
Alembic. The one-time machinery that adopted databases predating Alembic --
stamping in ``env.py`` plus a reconciliation revision 0002 -- was removed once
every real database had been migrated past it, which is why 0003 revises 0001.

Revision ID: 0001
Revises:
Create Date: 2026-07-10 10:44:31.882813

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=50), nullable=True),
    sa.Column('bio', sa.String(length=160), nullable=True),
    sa.Column('avatar_url', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('follows',
    sa.Column('follower_id', sa.Integer(), nullable=False),
    sa.Column('followee_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['followee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['follower_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('follower_id', 'followee_id', name='pk_follows')
    )
    with op.batch_alter_table('follows', schema=None) as batch_op:
        batch_op.create_index('ix_follows_followee_created', ['followee_id', 'created_at'], unique=False)

    op.create_table('posts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('media_urls', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reply_to_id', sa.Integer(), nullable=True),
    sa.Column('root_id', sa.Integer(), nullable=True),
    sa.Column('quoted_post_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['quoted_post_id'], ['posts.id'], ),
    sa.ForeignKeyConstraint(['reply_to_id'], ['posts.id'], ),
    sa.ForeignKeyConstraint(['root_id'], ['posts.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_posts_created_at'), ['created_at'], unique=False)
        batch_op.create_index('ix_posts_reply_created', ['reply_to_id', 'created_at'], unique=False)
        batch_op.create_index('ix_posts_root_created', ['root_id', 'created_at'], unique=False)
        batch_op.create_index('ix_posts_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_posts_user_id'), ['user_id'], unique=False)

    op.create_table('feed_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('post_id', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'post_id', name='uq_feed_owner_post')
    )
    with op.batch_alter_table('feed_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_feed_items_owner_id'), ['owner_id'], unique=False)
        batch_op.create_index('ix_feed_owner_created_id', ['owner_id', 'created_at', 'id'], unique=False)

    op.create_table('likes',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('post_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'post_id', name='pk_likes')
    )
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('post_id', sa.Integer(), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)
        batch_op.create_index('ix_notifications_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))
        batch_op.drop_index('ix_notifications_user_created')
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))

    op.drop_table('notifications')
    op.drop_table('likes')
    with op.batch_alter_table('feed_items', schema=None) as batch_op:
        batch_op.drop_index('ix_feed_owner_created_id')
        batch_op.drop_index(batch_op.f('ix_feed_items_owner_id'))

    op.drop_table('feed_items')
    with op.batch_alter_table('posts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_posts_user_id'))
        batch_op.drop_index('ix_posts_user_created')
        batch_op.drop_index('ix_posts_root_created')
        batch_op.drop_index('ix_posts_reply_created')
        batch_op.drop_index(batch_op.f('ix_posts_created_at'))

    op.drop_table('posts')
    with op.batch_alter_table('follows', schema=None) as batch_op:
        batch_op.drop_index('ix_follows_followee_created')

    op.drop_table('follows')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))

    op.drop_table('users')
