from datetime import datetime

from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, joinedload

from app.models.like import Like
from app.models.post import Post
from app.models.retweet import Retweet
from app.models.user import User


def create_tweet(
    db: Session,
    author_id: int,
    content: str,
    media_urls: list[str] | None = None,
) -> Post | None:
    """
    Create a top-level post (tweet) and reload it with author information.

    A top-level post is its own thread root, so ``root_id`` is set to the post's
    own id after it is assigned.
    """
    post = Post(user_id=author_id, content=content, media_urls=media_urls or None)
    db.add(post)
    db.flush()  # assign post.id
    post.root_id = post.id
    db.commit()

    return db.scalar(
        select(Post).options(joinedload(Post.author)).where(Post.id == post.id)
    )


def get_tweet(db: Session, tweet_id: int) -> Post | None:
    """Load one post with author information."""
    return db.scalar(
        select(Post).options(joinedload(Post.author)).where(Post.id == tweet_id)
    )


def _thread_reply_counts(post_ids: list[int]):
    """Subquery: number of replies in each post's whole thread (root_id match)."""
    return (
        select(
            Post.root_id.label("post_id"),
            func.count().label("comment_count"),
        )
        .where(Post.root_id.in_(post_ids), Post.id != Post.root_id)
        .group_by(Post.root_id)
        .subquery()
    )


def list_tweet_stats(
    db: Session,
    tweet_ids: list[int],
    current_user_id: int,
) -> list[dict]:
    """
    Return engagement stats for existing posts in the same order as requested.

    ``comment_count`` is the whole-thread reply count (Twitter's reply number on
    a tweet), matching the pre-unification behaviour.
    """
    ordered_ids = list(dict.fromkeys(tweet_ids))
    if not ordered_ids:
        return []

    like_counts = (
        select(Like.post_id, func.count().label("like_count"))
        .where(Like.post_id.in_(ordered_ids))
        .group_by(Like.post_id)
        .subquery()
    )
    comment_counts = _thread_reply_counts(ordered_ids)
    retweet_counts = (
        select(Retweet.post_id, func.count().label("retweet_count"))
        .where(Retweet.post_id.in_(ordered_ids))
        .group_by(Retweet.post_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Post.id,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(comment_counts, comment_counts.c.post_id == Post.id)
        .outerjoin(retweet_counts, retweet_counts.c.post_id == Post.id)
        .where(Post.id.in_(ordered_ids))
    ).all()

    liked_ids = {
        post_id
        for (post_id,) in db.execute(
            select(Like.post_id).where(
                Like.user_id == current_user_id,
                Like.post_id.in_(ordered_ids),
            )
        ).all()
    }

    stats_by_id = {
        post_id: {
            "id": post_id,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": post_id in liked_ids,
        }
        for post_id, like_count, comment_count, retweet_count in rows
    }
    return [stats_by_id[pid] for pid in ordered_ids if pid in stats_by_id]


def _load_tweet_rows_by_ids(
    db: Session,
    tweet_ids: list[int],
    current_user_id: int | None,
) -> dict[int, dict]:
    """Load posts (author + engagement counts) for the given ids, keyed by id."""
    if not tweet_ids:
        return {}

    unique_ids = list(dict.fromkeys(tweet_ids))

    like_counts = (
        select(Like.post_id, func.count().label("like_count"))
        .where(Like.post_id.in_(unique_ids))
        .group_by(Like.post_id)
        .subquery()
    )
    comment_counts = _thread_reply_counts(unique_ids)
    retweet_counts = (
        select(Retweet.post_id, func.count().label("retweet_count"))
        .where(Retweet.post_id.in_(unique_ids))
        .group_by(Retweet.post_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Post,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .options(joinedload(Post.author))
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(comment_counts, comment_counts.c.post_id == Post.id)
        .outerjoin(retweet_counts, retweet_counts.c.post_id == Post.id)
        .where(Post.id.in_(unique_ids))
    ).all()

    liked_ids: set[int] = set()
    if current_user_id is not None:
        liked_ids = {
            post_id
            for (post_id,) in db.execute(
                select(Like.post_id).where(
                    Like.user_id == current_user_id,
                    Like.post_id.in_(unique_ids),
                )
            ).all()
        }

    return {
        post.id: {
            "tweet": post,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": post.id in liked_ids,
        }
        for post, like_count, comment_count, retweet_count in rows
    }


def list_feed_with_retweets(
    db: Session,
    author_ids: list[int],
    limit: int,
    current_user_id: int | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Merge top-level tweets and retweeted tweets from a set of authors into one
    feed, ordered by activity time. Retweeted *comments* are intentionally
    excluded here — they belong to the replies feed — so only top-level posts
    participate. Retweet rows carry ``"retweeted_by"`` (the retweeting User).
    """
    if not author_ids:
        return []

    own = select(
        Post.id.label("tweet_id"),
        Post.created_at.label("activity_at"),
        literal(None).label("retweeter_id"),
    ).where(Post.user_id.in_(author_ids), Post.reply_to_id.is_(None))

    retweeted = (
        select(
            Retweet.post_id.label("tweet_id"),
            Retweet.created_at.label("activity_at"),
            Retweet.user_id.label("retweeter_id"),
        )
        .join(Post, Post.id == Retweet.post_id)
        .where(Retweet.user_id.in_(author_ids), Post.reply_to_id.is_(None))
    )

    return _assemble_activity_feed(
        db, own, retweeted, limit, current_user_id, cursor_created_at, cursor_id
    )


def _assemble_activity_feed(
    db: Session,
    own_select,
    retweet_select,
    limit: int,
    current_user_id: int | None,
    cursor_created_at: datetime | None,
    cursor_id: int | None,
) -> list[dict]:
    """Shared dedup + ordering + hydration for activity feeds (see callers)."""
    combined = union_all(own_select, retweet_select).subquery()

    ranked = select(
        combined.c.tweet_id,
        combined.c.activity_at,
        combined.c.retweeter_id,
        func.row_number()
        .over(
            partition_by=combined.c.tweet_id,
            order_by=(
                combined.c.activity_at.desc(),
                combined.c.retweeter_id.desc(),
            ),
        )
        .label("rn"),
    ).subquery()

    stmt = (
        select(ranked)
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.activity_at.desc(), ranked.c.tweet_id.desc())
        .limit(limit + 1)
    )
    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                ranked.c.activity_at < cursor_created_at,
                and_(
                    ranked.c.activity_at == cursor_created_at,
                    ranked.c.tweet_id < cursor_id,
                ),
            )
        )

    ident_rows = db.execute(stmt).all()
    tweet_rows = _load_tweet_rows_by_ids(
        db, [row.tweet_id for row in ident_rows], current_user_id
    )

    retweeter_ids = {r.retweeter_id for r in ident_rows if r.retweeter_id is not None}
    retweeters_by_id: dict[int, User] = {}
    if retweeter_ids:
        retweeters_by_id = {
            user.id: user
            for user in db.scalars(
                select(User).where(User.id.in_(retweeter_ids))
            ).all()
        }

    feed: list[dict] = []
    for row in ident_rows:
        base = tweet_rows.get(row.tweet_id)
        if base is None:
            continue
        feed.append(
            {
                **base,
                "retweeted_by": retweeters_by_id.get(row.retweeter_id)
                if row.retweeter_id is not None
                else None,
                "cursor_created_at": row.activity_at,
                "cursor_id": row.tweet_id,
            }
        )
    return feed


def list_for_you_tweets(
    db: Session,
    limit: int,
    current_user_id: int | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """Global feed of top-level tweets ordered by latest first, then engagement."""
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
            Post,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .options(joinedload(Post.author))
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(comment_counts, comment_counts.c.post_id == Post.id)
        .outerjoin(retweet_counts, retweet_counts.c.post_id == Post.id)
        .where(Post.reply_to_id.is_(None))
    )

    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Post.created_at < cursor_created_at,
                and_(
                    Post.created_at == cursor_created_at,
                    Post.id < cursor_id,
                ),
            )
        )

    rows = db.execute(stmt).all()
    liked_ids: set[int] = set()
    if current_user_id is not None:
        tweet_ids = [post.id for post, *_ in rows]
        if tweet_ids:
            liked_ids = {
                post_id
                for (post_id,) in db.execute(
                    select(Like.post_id).where(
                        Like.user_id == current_user_id,
                        Like.post_id.in_(tweet_ids),
                    )
                ).all()
            }

    scored_rows = [
        {
            "tweet": post,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": post.id in liked_ids,
            "score": int(like_count) * 3
            + int(retweet_count) * 4
            + int(comment_count) * 5,
            "cursor_created_at": post.created_at,
            "cursor_id": post.id,
        }
        for post, like_count, comment_count, retweet_count in rows
    ]

    scored_rows.sort(
        key=lambda row: (
            row["cursor_created_at"],
            row["score"],
            row["cursor_id"],
        ),
        reverse=True,
    )

    return scored_rows[: limit + 1]


def count_tweets_by_author(db: Session, author_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Post)
            .where(Post.user_id == author_id, Post.reply_to_id.is_(None))
        )
        or 0
    )
