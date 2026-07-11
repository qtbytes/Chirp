from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models.follow import Follow
from app.models.user import User
from app.repositories.block_repository import blocks_between
from app.repositories.notification_repository import add_notification


def follow_user(db: Session, follower_id: int, followee_id: int) -> Follow:
    """
    Create a follow relationship.

    Interview points:
    - Prevent self-follow.
    - Keep the operation idempotent: if the relation already exists,
      return the existing row instead of creating duplicates.
    """
    if follower_id == followee_id:
        raise ValueError("users cannot follow themselves")

    # A block, in either direction, forbids the follow. Checked here so every
    # follow path is covered, not just the HTTP route.
    if blocks_between(db, follower_id, followee_id):
        raise ValueError("cannot follow this user")

    existing = db.scalar(
        select(Follow).where(
            Follow.follower_id == follower_id,
            Follow.followee_id == followee_id,
        )
    )
    if existing:
        return existing

    relation = Follow(follower_id=follower_id, followee_id=followee_id)
    db.add(relation)
    add_notification(
        db,
        recipient_id=followee_id,
        actor_id=follower_id,
        type="follow",
    )
    db.commit()
    return relation


def unfollow_user(db: Session, follower_id: int, followee_id: int) -> bool:
    """
    Remove a follow relationship.

    Returns True if a row was deleted, otherwise False.
    """
    result = db.execute(
        delete(Follow).where(
            Follow.follower_id == follower_id,
            Follow.followee_id == followee_id,
        )
    )
    db.commit()
    return result.rowcount > 0


def list_followee_ids(db: Session, follower_id: int) -> list[int]:
    """
    Return all users that the given user follows.

    Used by fan-out on read timeline queries.
    """
    rows = db.execute(
        select(Follow.followee_id).where(Follow.follower_id == follower_id)
    ).all()
    return [row[0] for row in rows]


def list_follower_ids(db: Session, followee_id: int) -> list[int]:
    """
    Return all followers of the given user.

    Used by fan-out on write when pushing a new tweet into followers' feeds.
    """
    rows = db.execute(
        select(Follow.follower_id).where(Follow.followee_id == followee_id)
    ).all()
    return [row[0] for row in rows]


def count_followers(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Follow)
            .where(Follow.followee_id == user_id)
        )
        or 0
    )


def count_following(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Follow)
            .where(Follow.follower_id == user_id)
        )
        or 0
    )


def _followed_subset(
    db: Session, current_user_id: int, user_ids: list[int]
) -> set[int]:
    """Which of ``user_ids`` the current user already follows, in one query."""
    if not user_ids:
        return set()
    return {
        followee_id
        for (followee_id,) in db.execute(
            select(Follow.followee_id).where(
                Follow.follower_id == current_user_id,
                Follow.followee_id.in_(user_ids),
            )
        ).all()
    }


def _list_follow_edge(
    db: Session,
    *,
    pivot_column: InstrumentedAttribute,
    pivot_value: int,
    listed_column: InstrumentedAttribute,
    current_user_id: int,
    limit: int,
    cursor_created_at: datetime | None,
    cursor_id: int | None,
    exclude_user_ids: set[int] | None = None,
) -> list[dict]:
    """
    One side of the follow graph, newest edge first.

    ``pivot_column`` is the fixed end of the edge (the profile being viewed) and
    ``listed_column`` is the end joined to ``User`` and returned. Followers and
    following are the same query with those two swapped.

    Fetches ``limit + 1`` to let the caller detect a next page, and paginates by
    ``(Follow.created_at, User.id)`` -- a stable, unique key, so an unfollow
    landing mid-scroll cannot skip or duplicate a row the way an offset would.
    """
    stmt = (
        select(User, Follow.created_at)
        .join(Follow, listed_column == User.id)
        .where(pivot_column == pivot_value)
    )
    # Blocked (and blocking) users are omitted from follower/following lists.
    if exclude_user_ids:
        stmt = stmt.where(User.id.not_in(exclude_user_ids))
    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Follow.created_at < cursor_created_at,
                and_(Follow.created_at == cursor_created_at, User.id < cursor_id),
            )
        )
    stmt = stmt.order_by(Follow.created_at.desc(), User.id.desc()).limit(limit + 1)

    rows = db.execute(stmt).all()
    followed = _followed_subset(db, current_user_id, [user.id for user, _ in rows])
    return [
        {
            "user": user,
            "follow_created_at": created_at,
            "is_following": user.id in followed,
        }
        for user, created_at in rows
    ]


def list_followers(
    db: Session,
    user_id: int,
    current_user_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
    exclude_user_ids: set[int] | None = None,
) -> list[dict]:
    """Users who follow ``user_id``, most recently followed first."""
    return _list_follow_edge(
        db,
        pivot_column=Follow.followee_id,
        pivot_value=user_id,
        listed_column=Follow.follower_id,
        current_user_id=current_user_id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        exclude_user_ids=exclude_user_ids,
    )


def list_following(
    db: Session,
    user_id: int,
    current_user_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
    exclude_user_ids: set[int] | None = None,
) -> list[dict]:
    """Users ``user_id`` follows, most recently followed first."""
    return _list_follow_edge(
        db,
        pivot_column=Follow.follower_id,
        pivot_value=user_id,
        listed_column=Follow.followee_id,
        current_user_id=current_user_id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        exclude_user_ids=exclude_user_ids,
    )


def is_following(db: Session, follower_id: int, followee_id: int) -> bool:
    return (
        db.scalar(
            select(Follow).where(
                Follow.follower_id == follower_id,
                Follow.followee_id == followee_id,
            )
        )
        is not None
    )
