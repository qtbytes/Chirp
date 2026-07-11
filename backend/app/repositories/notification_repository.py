from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.post import Post
from app.models.user import User
from app.services import events


def add_notification(
    db: Session,
    *,
    recipient_id: int,
    actor_id: int,
    type: str,
    post_id: int | None = None,
) -> None:
    """
    Stage a notification on the current session (no commit).

    The caller's existing commit persists it, so the notification is written in
    the same transaction as the action that triggered it. Self-actions (e.g.
    liking your own post) never notify.
    """
    if recipient_id == actor_id:
        return

    db.add(
        Notification(
            user_id=recipient_id,
            actor_id=actor_id,
            type=type,
            post_id=post_id,
        )
    )
    # Nudge the recipient's live stream -- but only once this transaction
    # commits, so a rolled-back action never wakes anyone.
    events.queue_user_event(db, recipient_id)


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


def mark_read(db: Session, user_id: int, notification_id: int) -> bool:
    """
    Mark one notification read. Returns False if it does not exist or is not the
    caller's -- the ``user_id`` guard is what stops reading someone else's row.
    Idempotent: re-reading an already-read notification still returns True.
    """
    result = db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        .values(is_read=True)
    )
    db.commit()
    return (result.rowcount or 0) > 0


def list_notifications(
    db: Session,
    user_id: int,
    limit: int = 30,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return the recipient's most recent notifications, each joined with its actor
    and a short content preview of the related post (one batched query for the
    posts to avoid N+1).

    Fetches ``limit + 1`` and paginates by ``(created_at, id)`` like the feeds, so
    the caller can detect a next page and older notifications can't be skipped or
    repeated as new ones arrive.

    ``tweet_id`` / ``comment_id`` are reconstructed from the related post so the
    client keeps its existing linking behaviour: a reply reports both its own id
    (comment_id) and its thread root (tweet_id); a top-level post reports only
    tweet_id.
    """
    stmt = (
        select(Notification, User)
        .join(User, User.id == Notification.actor_id)
        .where(Notification.user_id == user_id)
    )
    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Notification.created_at < cursor_created_at,
                and_(
                    Notification.created_at == cursor_created_at,
                    Notification.id < cursor_id,
                ),
            )
        )
    rows = db.execute(
        stmt.order_by(
            Notification.created_at.desc(), Notification.id.desc()
        ).limit(limit + 1)
    ).all()

    post_ids = {n.post_id for n, _ in rows if n.post_id is not None}
    posts_by_id: dict[int, Post] = {}
    if post_ids:
        posts_by_id = {
            post.id: post
            for post in db.scalars(
                select(Post).where(Post.id.in_(post_ids))
            ).all()
        }

    results: list[dict] = []
    for notification, actor in rows:
        post = (
            posts_by_id.get(notification.post_id)
            if notification.post_id is not None
            else None
        )
        tweet_id: int | None = None
        comment_id: int | None = None
        preview: str | None = None
        if post is not None:
            preview = post.content
            if post.reply_to_id is not None:
                comment_id = post.id
                tweet_id = post.root_id
            else:
                tweet_id = post.id
        results.append(
            {
                "id": notification.id,
                "type": notification.type,
                "actor": actor,
                "tweet_id": tweet_id,
                "comment_id": comment_id,
                "preview": preview,
                "is_read": notification.is_read,
                "created_at": notification.created_at,
            }
        )
    return results
