from datetime import datetime

from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, joinedload

from app.models.comment import Comment
from app.models.like import Like
from app.models.retweet import Retweet
from app.models.tweet import Tweet
from app.models.user import User


def create_tweet(
    db: Session,
    author_id: int,
    content: str,
    media_urls: list[str] | None = None,
) -> Tweet | None:
    """
    Create a tweet and reload it with author information.

    Why reload?
    - API response usually needs author data.
    - This avoids a later lazy-load when serializing the tweet.
    """
    tweet = Tweet(user_id=author_id, content=content, media_urls=media_urls or None)
    db.add(tweet)
    db.commit()
    db.refresh(tweet)

    return db.scalar(
        select(Tweet).options(joinedload(Tweet.author)).where(Tweet.id == tweet.id)
    )


def get_tweet(db: Session, tweet_id: int) -> Tweet | None:
    """
    Load one tweet with author information.
    """
    return db.scalar(
        select(Tweet).options(joinedload(Tweet.author)).where(Tweet.id == tweet_id)
    )


def list_tweet_stats(
    db: Session,
    tweet_ids: list[int],
    current_user_id: int,
) -> list[dict]:
    """
    Return engagement stats for existing tweets in the same order as requested.
    """
    ordered_ids = list(dict.fromkeys(tweet_ids))
    if not ordered_ids:
        return []

    like_counts = (
        select(
            Like.tweet_id,
            func.count().label("like_count"),
        )
        .where(Like.tweet_id.in_(ordered_ids))
        .group_by(Like.tweet_id)
        .subquery()
    )

    comment_counts = (
        select(
            Comment.tweet_id,
            func.count().label("comment_count"),
        )
        .where(Comment.tweet_id.in_(ordered_ids))
        .group_by(Comment.tweet_id)
        .subquery()
    )

    retweet_counts = (
        select(
            Retweet.tweet_id,
            func.count().label("retweet_count"),
        )
        .where(Retweet.tweet_id.in_(ordered_ids))
        .group_by(Retweet.tweet_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Tweet.id,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .outerjoin(like_counts, like_counts.c.tweet_id == Tweet.id)
        .outerjoin(comment_counts, comment_counts.c.tweet_id == Tweet.id)
        .outerjoin(retweet_counts, retweet_counts.c.tweet_id == Tweet.id)
        .where(Tweet.id.in_(ordered_ids))
    ).all()

    liked_tweet_ids = {
        tweet_id
        for (tweet_id,) in db.execute(
            select(Like.tweet_id).where(
                Like.user_id == current_user_id,
                Like.tweet_id.in_(ordered_ids),
            )
        ).all()
    }

    stats_by_id = {
        tweet_id: {
            "id": tweet_id,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": tweet_id in liked_tweet_ids,
        }
        for tweet_id, like_count, comment_count, retweet_count in rows
    }
    return [stats_by_id[tweet_id] for tweet_id in ordered_ids if tweet_id in stats_by_id]


def list_tweets_by_authors(
    db: Session,
    author_ids: list[int],
    limit: int,
    current_user_id: int | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Read tweets for fan-out on read timeline.

    Interview focus:
    - Uses cursor pagination instead of offset pagination.
    - Avoids N+1 by eager-loading author and aggregating like/comment counts
      in the same query.
    - Orders by (created_at DESC, id DESC) so pagination stays stable even
      when multiple tweets have the same timestamp.

    Returns:
        A list of dictionaries shaped for the timeline service:
        {
            "tweet": Tweet,
            "like_count": int,
            "comment_count": int,
            "cursor_created_at": datetime,
            "cursor_id": int,
        }
    """
    if not author_ids:
        return []

    like_counts = (
        select(
            Like.tweet_id,
            func.count().label("like_count"),
        )
        .group_by(Like.tweet_id)
        .subquery()
    )

    comment_counts = (
        select(
            Comment.tweet_id,
            func.count().label("comment_count"),
        )
        .group_by(Comment.tweet_id)
        .subquery()
    )

    retweet_counts = (
        select(
            Retweet.tweet_id,
            func.count().label("retweet_count"),
        )
        .group_by(Retweet.tweet_id)
        .subquery()
    )
    liked_tweet_ids: set[int] = set()

    stmt = (
        select(
            Tweet,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .options(joinedload(Tweet.author))
        .outerjoin(like_counts, like_counts.c.tweet_id == Tweet.id)
        .outerjoin(comment_counts, comment_counts.c.tweet_id == Tweet.id)
        .outerjoin(retweet_counts, retweet_counts.c.tweet_id == Tweet.id)
        .where(Tweet.user_id.in_(author_ids))
        .order_by(Tweet.created_at.desc(), Tweet.id.desc())
        .limit(limit + 1)
    )

    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Tweet.created_at < cursor_created_at,
                and_(
                    Tweet.created_at == cursor_created_at,
                    Tweet.id < cursor_id,
                ),
            )
        )

    rows = db.execute(stmt).all()
    if current_user_id is not None:
        tweet_ids = [tweet.id for tweet, *_ in rows]
        if tweet_ids:
            liked_tweet_ids = {
                tweet_id
                for (tweet_id,) in db.execute(
                    select(Like.tweet_id).where(
                        Like.user_id == current_user_id,
                        Like.tweet_id.in_(tweet_ids),
                    )
                ).all()
            }

    return [
        {
            "tweet": tweet,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": tweet.id in liked_tweet_ids,
            "cursor_created_at": tweet.created_at,
            "cursor_id": tweet.id,
        }
        for tweet, like_count, comment_count, retweet_count in rows
    ]


def list_for_you_tweets(
    db: Session,
    limit: int,
    current_user_id: int | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return a global feed ordered by latest first, then engagement score.
    """
    like_counts = (
        select(
            Like.tweet_id,
            func.count().label("like_count"),
        )
        .group_by(Like.tweet_id)
        .subquery()
    )

    comment_counts = (
        select(
            Comment.tweet_id,
            func.count().label("comment_count"),
        )
        .group_by(Comment.tweet_id)
        .subquery()
    )

    retweet_counts = (
        select(
            Retweet.tweet_id,
            func.count().label("retweet_count"),
        )
        .group_by(Retweet.tweet_id)
        .subquery()
    )

    stmt = (
        select(
            Tweet,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .options(joinedload(Tweet.author))
        .outerjoin(like_counts, like_counts.c.tweet_id == Tweet.id)
        .outerjoin(comment_counts, comment_counts.c.tweet_id == Tweet.id)
        .outerjoin(retweet_counts, retweet_counts.c.tweet_id == Tweet.id)
    )

    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Tweet.created_at < cursor_created_at,
                and_(
                    Tweet.created_at == cursor_created_at,
                    Tweet.id < cursor_id,
                ),
            )
        )

    rows = db.execute(stmt).all()
    liked_tweet_ids: set[int] = set()
    if current_user_id is not None:
        tweet_ids = [tweet.id for tweet, *_ in rows]
        if tweet_ids:
            liked_tweet_ids = {
                tweet_id
                for (tweet_id,) in db.execute(
                    select(Like.tweet_id).where(
                        Like.user_id == current_user_id,
                        Like.tweet_id.in_(tweet_ids),
                    )
                ).all()
            }

    scored_rows = [
            {
                "tweet": tweet,
                "like_count": int(like_count),
                "comment_count": int(comment_count),
                "retweet_count": int(retweet_count),
                "liked_by_me": tweet.id in liked_tweet_ids,
                "score": int(like_count) * 3
                + int(retweet_count) * 4
                + int(comment_count) * 5,
                "cursor_created_at": tweet.created_at,
                "cursor_id": tweet.id,
            }
        for tweet, like_count, comment_count, retweet_count in rows
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


def _load_tweet_rows_by_ids(
    db: Session,
    tweet_ids: list[int],
    current_user_id: int | None,
) -> dict[int, dict]:
    """
    Load tweets (with author + engagement counts) for the given ids.

    Returns a mapping of tweet id -> row dict, so callers can assemble
    results in whatever order they need.
    """
    if not tweet_ids:
        return {}

    unique_ids = list(dict.fromkeys(tweet_ids))

    like_counts = (
        select(Like.tweet_id, func.count().label("like_count"))
        .where(Like.tweet_id.in_(unique_ids))
        .group_by(Like.tweet_id)
        .subquery()
    )
    comment_counts = (
        select(Comment.tweet_id, func.count().label("comment_count"))
        .where(Comment.tweet_id.in_(unique_ids))
        .group_by(Comment.tweet_id)
        .subquery()
    )
    retweet_counts = (
        select(Retweet.tweet_id, func.count().label("retweet_count"))
        .where(Retweet.tweet_id.in_(unique_ids))
        .group_by(Retweet.tweet_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Tweet,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .options(joinedload(Tweet.author))
        .outerjoin(like_counts, like_counts.c.tweet_id == Tweet.id)
        .outerjoin(comment_counts, comment_counts.c.tweet_id == Tweet.id)
        .outerjoin(retweet_counts, retweet_counts.c.tweet_id == Tweet.id)
        .where(Tweet.id.in_(unique_ids))
    ).all()

    liked_tweet_ids: set[int] = set()
    if current_user_id is not None:
        liked_tweet_ids = {
            tweet_id
            for (tweet_id,) in db.execute(
                select(Like.tweet_id).where(
                    Like.user_id == current_user_id,
                    Like.tweet_id.in_(unique_ids),
                )
            ).all()
        }

    return {
        tweet.id: {
            "tweet": tweet,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": tweet.id in liked_tweet_ids,
        }
        for tweet, like_count, comment_count, retweet_count in rows
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
    Merge original tweets and retweets from a set of authors into one feed.

    Each entry is ordered by "activity time" — the tweet's creation time for
    an original tweet, or the retweet time for a retweeted tweet — so a
    retweet surfaces at the moment it was retweeted (Twitter behaviour).

    Used for both a single user's profile feed and the home timeline (where
    the authors are the viewer plus everyone they follow). Retweet rows carry
    ``"retweeted_by"`` set to the retweeting :class:`User` so the caller can
    render a "retweeted by" marker.
    """
    if not author_ids:
        return []

    own = select(
        Tweet.id.label("tweet_id"),
        Tweet.created_at.label("activity_at"),
        literal(None).label("retweeter_id"),
    ).where(Tweet.user_id.in_(author_ids))

    retweeted = select(
        Retweet.tweet_id.label("tweet_id"),
        Retweet.created_at.label("activity_at"),
        Retweet.user_id.label("retweeter_id"),
    ).where(Retweet.user_id.in_(author_ids))

    combined = union_all(own, retweeted).subquery()

    # A tweet may appear multiple times (original post plus retweets by
    # different followed users). Keep only its most recent activity so it
    # shows once — as a retweet if that was the latest action, else as the
    # original. Deduping in SQL keeps cursor pagination correct.
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
        db,
        [row.tweet_id for row in ident_rows],
        current_user_id,
    )

    retweeter_ids = {row.retweeter_id for row in ident_rows if row.retweeter_id is not None}
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


def count_tweets_by_author(db: Session, author_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Tweet)
            .where(Tweet.user_id == author_id)
        )
        or 0
    )
