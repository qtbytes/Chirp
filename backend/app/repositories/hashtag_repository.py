"""Aggregate queries over the ``post_hashtags`` entity rows."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.user import User


def list_trending(db: Session, window_hours: int, limit: int) -> list[dict]:
    """
    The ``limit`` most-used hashtags within the last ``window_hours``.

    A global aggregate: ranked by how many posts used each tag in the window,
    ties broken alphabetically so the order is stable. Rows from deleted
    (tombstoned) accounts are excluded so their content cannot drive a trend.
    Blocks are deliberately *not* applied -- they are viewer-specific, and
    trending is one figure shared (and cached) across all viewers.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    rows = db.execute(
        select(PostHashtag.tag, func.count().label("post_count"))
        .join(Post, Post.id == PostHashtag.post_id)
        .join(User, User.id == Post.user_id)
        .where(PostHashtag.created_at >= cutoff, User.deleted_at.is_(None))
        .group_by(PostHashtag.tag)
        .order_by(func.count().desc(), PostHashtag.tag.asc())
        .limit(limit)
    ).all()

    return [{"tag": tag, "post_count": int(count)} for tag, count in rows]
