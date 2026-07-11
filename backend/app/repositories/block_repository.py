from datetime import datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models.block import Block
from app.models.follow import Follow
from app.models.user import User
from app.repositories import mute_repository


def block_user(db: Session, blocker_id: int, blocked_id: int) -> Block:
    """
    Block a user, idempotently, and sever any follow between the two.

    A block that left the follow edges in place would be a lie: the blocked user
    would keep appearing in the blocker's following list and keep receiving their
    posts through fan-out. So both directions of the follow are deleted here, in
    the same transaction as the block.
    """
    if blocker_id == blocked_id:
        raise ValueError("users cannot block themselves")

    existing = db.scalar(
        select(Block).where(
            Block.blocker_id == blocker_id, Block.blocked_id == blocked_id
        )
    )
    if existing is not None:
        return existing

    block = Block(blocker_id=blocker_id, blocked_id=blocked_id)
    db.add(block)
    db.execute(
        delete(Follow).where(
            or_(
                and_(
                    Follow.follower_id == blocker_id,
                    Follow.followee_id == blocked_id,
                ),
                and_(
                    Follow.follower_id == blocked_id,
                    Follow.followee_id == blocker_id,
                ),
            )
        )
    )
    db.commit()
    return block


def unblock_user(db: Session, blocker_id: int, blocked_id: int) -> bool:
    """Remove a block. Returns True if one existed. Follows are not restored."""
    result = db.execute(
        delete(Block).where(
            Block.blocker_id == blocker_id, Block.blocked_id == blocked_id
        )
    )
    db.commit()
    return result.rowcount > 0


def is_blocking(db: Session, blocker_id: int, blocked_id: int) -> bool:
    """Whether ``blocker_id`` blocks ``blocked_id`` -- one direction only."""
    return (
        db.scalar(
            select(Block.blocker_id).where(
                Block.blocker_id == blocker_id,
                Block.blocked_id == blocked_id,
            )
        )
        is not None
    )


def blocks_between(db: Session, a_id: int, b_id: int) -> bool:
    """
    Whether a block exists in *either* direction between two users.

    This is the write-path guard: neither a blocker nor a blocked user may
    follow, like, comment on, or quote the other, so interactions check this.
    """
    if a_id == b_id:
        return False
    return (
        db.scalar(
            select(Block.blocker_id).where(
                or_(
                    and_(Block.blocker_id == a_id, Block.blocked_id == b_id),
                    and_(Block.blocker_id == b_id, Block.blocked_id == a_id),
                )
            )
        )
        is not None
    )


def hidden_user_ids(db: Session, viewer_id: int) -> set[int]:
    """
    Every user hidden from ``viewer_id``: those they blocked, unioned with those
    who blocked them, unioned with those they muted. This is the single set the
    read paths filter authors by.

    Blocks contribute in both directions (a block hides each party from the
    other); mutes contribute in one (a mute only hides the muted user from the
    muter, never the reverse). Because mutes ride this same set, every read path
    that already excludes blocked authors -- timeline, for-you, thread comments,
    notifications, discovery -- excludes muted authors for free.
    """
    stmt = select(Block.blocked_id).where(Block.blocker_id == viewer_id).union(
        select(Block.blocker_id).where(Block.blocked_id == viewer_id)
    )
    hidden = {row[0] for row in db.execute(stmt).all()}
    hidden |= mute_repository.muted_user_ids(db, viewer_id)
    return hidden


def list_blocked(
    db: Session,
    blocker_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Users ``blocker_id`` has blocked, most recently blocked first, for the
    account's own management screen. Fetches ``limit + 1`` for a next-page probe
    and paginates by ``(created_at, user id)`` like every other list here.
    """
    ordering_created_at: InstrumentedAttribute = Block.created_at
    stmt = (
        select(User, Block.created_at)
        .join(Block, Block.blocked_id == User.id)
        .where(Block.blocker_id == blocker_id)
    )
    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                ordering_created_at < cursor_created_at,
                and_(
                    ordering_created_at == cursor_created_at,
                    User.id < cursor_id,
                ),
            )
        )
    rows = db.execute(
        stmt.order_by(ordering_created_at.desc(), User.id.desc()).limit(limit + 1)
    ).all()
    return [
        {"user": user, "blocked_at": created_at} for user, created_at in rows
    ]
