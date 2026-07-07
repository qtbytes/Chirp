from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.comment import Comment
from app.models.notification import Notification
from app.models.tweet import Tweet
from app.models.user import User


def add_notification(
    db: Session,
    *,
    recipient_id: int,
    actor_id: int,
    type: str,
    tweet_id: int | None = None,
    comment_id: int | None = None,
) -> None:
    """
    Stage a notification on the current session (no commit).

    The caller's existing commit persists it, so the notification is written in
    the same transaction as the action that triggered it. Self-actions (e.g.
    liking your own tweet) never notify.
    """
    if recipient_id == actor_id:
        return

    db.add(
        Notification(
            user_id=recipient_id,
            actor_id=actor_id,
            type=type,
            tweet_id=tweet_id,
            comment_id=comment_id,
        )
    )


def count_unread(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        )
        or 0
    )


def mark_all_read(db: Session, user_id: int) -> int:
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()
    return result.rowcount or 0


def list_notifications(
    db: Session,
    user_id: int,
    limit: int = 30,
) -> list[dict]:
    """
    Return the recipient's most recent notifications, each joined with its actor
    and a short content preview of the related tweet/comment (one batched query
    per kind to avoid N+1).
    """
    rows = db.execute(
        select(Notification, User)
        .join(User, User.id == Notification.actor_id)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()

    tweet_ids = {n.tweet_id for n, _ in rows if n.tweet_id is not None}
    comment_ids = {n.comment_id for n, _ in rows if n.comment_id is not None}

    tweet_previews: dict[int, str] = {}
    if tweet_ids:
        tweet_previews = {
            row.id: row.content
            for row in db.execute(
                select(Tweet.id, Tweet.content).where(Tweet.id.in_(tweet_ids))
            ).all()
        }
    comment_previews: dict[int, str] = {}
    if comment_ids:
        comment_previews = {
            row.id: row.content
            for row in db.execute(
                select(Comment.id, Comment.content).where(Comment.id.in_(comment_ids))
            ).all()
        }

    results: list[dict] = []
    for notification, actor in rows:
        preview = None
        if notification.comment_id is not None:
            preview = comment_previews.get(notification.comment_id)
        elif notification.tweet_id is not None:
            preview = tweet_previews.get(notification.tweet_id)
        results.append(
            {
                "id": notification.id,
                "type": notification.type,
                "actor": actor,
                "tweet_id": notification.tweet_id,
                "comment_id": notification.comment_id,
                "preview": preview,
                "is_read": notification.is_read,
                "created_at": notification.created_at,
            }
        )
    return results
