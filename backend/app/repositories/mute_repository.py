from datetime import datetime

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models.mute import Mute
from app.models.user import User


def mute_user(db: Session, muter_id: int, muted_id: int) -> Mute:
    """
    Mute a user, idempotently.

    Unlike a block, a mute leaves every follow edge in place: muting is a way to
    stop seeing someone without unfollowing them or letting them know. So this
    touches only the ``mutes`` table -- no follow rows, no reverse effect.
    """
    if muter_id == muted_id:
        raise ValueError("users cannot mute themselves")

    existing = db.scalar(
        select(Mute).where(Mute.muter_id == muter_id, Mute.muted_id == muted_id)
    )
    if existing is not None:
        return existing

    mute = Mute(muter_id=muter_id, muted_id=muted_id)
    db.add(mute)
    db.commit()
    return mute


def unmute_user(db: Session, muter_id: int, muted_id: int) -> bool:
    """Remove a mute. Returns True if one existed."""
    result = db.execute(
        delete(Mute).where(Mute.muter_id == muter_id, Mute.muted_id == muted_id)
    )
    db.commit()
    return result.rowcount > 0


def is_muting(db: Session, muter_id: int, muted_id: int) -> bool:
    """Whether ``muter_id`` mutes ``muted_id`` -- the only direction a mute has."""
    return (
        db.scalar(
            select(Mute.muter_id).where(
                Mute.muter_id == muter_id,
                Mute.muted_id == muted_id,
            )
        )
        is not None
    )


def muted_user_ids(db: Session, muter_id: int) -> set[int]:
    """
    Every user ``muter_id`` has muted. One-directional: muting is never unioned
    with a reverse edge the way blocks are, because a mute has no effect on the
    muted user's own view. Folded into block_repository.hidden_user_ids so the
    read paths filter muted authors with the same plumbing they use for blocks.
    """
    stmt = select(Mute.muted_id).where(Mute.muter_id == muter_id)
    return {row[0] for row in db.execute(stmt).all()}


def list_muted(
    db: Session,
    muter_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Users ``muter_id`` has muted, most recently muted first, for the account's
    own management screen. Mirrors block_repository.list_blocked: fetches
    ``limit + 1`` for a next-page probe and paginates by ``(created_at, user id)``.
    """
    ordering_created_at: InstrumentedAttribute = Mute.created_at
    stmt = (
        select(User, Mute.created_at)
        .join(Mute, Mute.muted_id == User.id)
        .where(Mute.muter_id == muter_id)
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
    return [{"user": user, "muted_at": created_at} for user, created_at in rows]
