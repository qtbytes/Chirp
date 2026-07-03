from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.follow import Follow


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
