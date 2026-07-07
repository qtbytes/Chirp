from datetime import datetime

from sqlalchemy import and_, func, insert, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.feed import FeedItem
from app.models.like import Like
from app.models.post import Post
from app.models.retweet import Retweet


def bulk_insert_feed_items(
    db: Session,
    owner_ids: list[int],
    post_id: int,
    actor_id: int,
    created_at: datetime,
) -> int:
    """
    Fan-out on write:
    insert one feed row per timeline owner.

    Notes for interview:
    - We deduplicate owner_ids first.
    - We check existing rows to keep the operation idempotent.
    - We chunk existence checks and inserts so SQLite / DB parameter limits
      do not break very large celebrity fan-out workloads.
    - In high-concurrency production systems, this is often moved to
      a background worker and may use bulk SQL / upsert.
    """
    unique_owner_ids = list(dict.fromkeys(owner_ids))
    if not unique_owner_ids:
        return 0

    chunk_size = 500
    total_inserted = 0

    for start in range(0, len(unique_owner_ids), chunk_size):
        owner_id_chunk = unique_owner_ids[start : start + chunk_size]

        existing_owner_ids = {
            owner_id
            for (owner_id,) in db.execute(
                select(FeedItem.owner_id).where(
                    FeedItem.post_id == post_id,
                    FeedItem.owner_id.in_(owner_id_chunk),
                )
            ).all()
        }

        payload = [
            {
                "owner_id": owner_id,
                "post_id": post_id,
                "actor_id": actor_id,
                "created_at": created_at,
            }
            for owner_id in owner_id_chunk
            if owner_id not in existing_owner_ids
        ]

        if not payload:
            continue

        db.execute(insert(FeedItem), payload)
        total_inserted += len(payload)

    db.commit()
    return total_inserted


def list_feed_tweets(
    db: Session,
    owner_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Read precomputed home timeline rows for fan-out on write.

    Why this query matters in interviews:
    - It uses cursor pagination instead of offset pagination.
    - It avoids N+1 for author data with joinedload.
    - It aggregates likes/comments in SQL instead of per-row queries.
    """
    like_counts = (
        select(Like.post_id, func.count().label("like_count"))
        .group_by(Like.post_id)
        .subquery()
    )
    comment_counts = (
        select(Post.root_id.label("post_id"), func.count().label("comment_count"))
        .where(Post.id != Post.root_id)
        .group_by(Post.root_id)
        .subquery()
    )
    retweet_counts = (
        select(Retweet.post_id, func.count().label("retweet_count"))
        .group_by(Retweet.post_id)
        .subquery()
    )

    stmt = (
        select(
            FeedItem,
            Post,
            func.coalesce(like_counts.c.like_count, 0),
            func.coalesce(comment_counts.c.comment_count, 0),
            func.coalesce(retweet_counts.c.retweet_count, 0),
        )
        .join(Post, Post.id == FeedItem.post_id)
        .options(joinedload(Post.author))
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(comment_counts, comment_counts.c.post_id == Post.id)
        .outerjoin(retweet_counts, retweet_counts.c.post_id == Post.id)
        .where(FeedItem.owner_id == owner_id)
        .order_by(FeedItem.created_at.desc(), FeedItem.id.desc())
        .limit(limit + 1)
    )

    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                FeedItem.created_at < cursor_created_at,
                and_(
                    FeedItem.created_at == cursor_created_at,
                    FeedItem.id < cursor_id,
                ),
            )
        )

    rows = db.execute(stmt).all()
    post_ids = [post.id for _, post, *_ in rows]
    liked_ids = set()
    if post_ids:
        liked_ids = {
            post_id
            for (post_id,) in db.execute(
                select(Like.post_id).where(
                    Like.user_id == owner_id,
                    Like.post_id.in_(post_ids),
                )
            ).all()
        }

    return [
        {
            "tweet": post,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": post.id in liked_ids,
            "cursor_created_at": feed_item.created_at,
            "cursor_id": feed_item.id,
        }
        for feed_item, post, like_count, comment_count, retweet_count in rows
    ]
