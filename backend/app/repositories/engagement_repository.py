from datetime import datetime

from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from app.models.comment import Comment
from app.models.comment_like import CommentLike
from app.models.comment_retweet import CommentRetweet
from app.models.like import Like
from app.models.retweet import Retweet
from app.models.tweet import Tweet
from app.models.user import User
from app.repositories.notification_repository import add_notification


def like_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    """
    Create a like record for a tweet.

    Returns:
    - True: a new like was created
    - False: the user had already liked the tweet

    Interview points:
    - Keep the operation idempotent.
    - Validate the target tweet exists.
    - In production, combine this with a unique constraint and possibly Redis counters.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    existing = db.scalar(
        select(Like).where(
            Like.user_id == user_id,
            Like.tweet_id == tweet_id,
        )
    )
    if existing:
        return False

    like = Like(user_id=user_id, tweet_id=tweet_id)
    db.add(like)
    add_notification(
        db,
        recipient_id=tweet.user_id,
        actor_id=user_id,
        type="like",
        tweet_id=tweet_id,
    )
    db.commit()
    return True


def unlike_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    """
    Remove a like from a tweet.

    Returns:
    - True: a like existed and was removed
    - False: no like existed
    """
    existing = db.scalar(
        select(Like).where(
            Like.user_id == user_id,
            Like.tweet_id == tweet_id,
        )
    )
    if existing is None:
        return False

    db.delete(existing)
    db.commit()
    return True


def count_tweet_likes(db: Session, tweet_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Like).where(Like.tweet_id == tweet_id))
        or 0
    )


def has_liked_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    return (
        db.scalar(
            select(Like).where(
                Like.user_id == user_id,
                Like.tweet_id == tweet_id,
            )
        )
        is not None
    )


def retweet_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    """
    Create a retweet record.

    Returns True when a new retweet is created and False when the user had
    already retweeted the tweet.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    existing = db.scalar(
        select(Retweet).where(
            Retweet.user_id == user_id,
            Retweet.tweet_id == tweet_id,
        )
    )
    if existing:
        return False

    retweet = Retweet(user_id=user_id, tweet_id=tweet_id)
    db.add(retweet)
    add_notification(
        db,
        recipient_id=tweet.user_id,
        actor_id=user_id,
        type="retweet",
        tweet_id=tweet_id,
    )
    db.commit()
    return True


def unretweet_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    """
    Remove a retweet record.

    Returns True if a retweet existed and was removed, otherwise False.
    """
    existing = db.scalar(
        select(Retweet).where(
            Retweet.user_id == user_id,
            Retweet.tweet_id == tweet_id,
        )
    )
    if existing is None:
        return False

    db.delete(existing)
    db.commit()
    return True


def create_comment(
    db: Session,
    user_id: int,
    tweet_id: int,
    content: str,
    parent_comment_id: int | None = None,
    media_urls: list[str] | None = None,
) -> tuple[Comment, User]:
    """
    Create a comment on a tweet and return the comment with its author.

    Interview points:
    - Validate the tweet exists before inserting.
    - Return the author together to avoid extra lookups in upper layers.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    author = db.get(User, user_id)
    if author is None:
        raise ValueError("user not found")

    parent_comment = None
    if parent_comment_id is not None:
        parent_comment = db.get(Comment, parent_comment_id)
        if parent_comment is None or parent_comment.tweet_id != tweet_id:
            raise ValueError("parent comment not found")

    comment = Comment(
        user_id=user_id,
        tweet_id=tweet_id,
        parent_comment_id=parent_comment_id,
        content=content,
        media_urls=media_urls or None,
    )
    db.add(comment)
    db.flush()  # assign comment.id before staging the notification

    # A reply notifies the parent comment's author; a top-level comment notifies
    # the tweet's author.
    if parent_comment is not None:
        add_notification(
            db,
            recipient_id=parent_comment.user_id,
            actor_id=user_id,
            type="reply",
            tweet_id=tweet_id,
            comment_id=comment.id,
        )
    else:
        add_notification(
            db,
            recipient_id=tweet.user_id,
            actor_id=user_id,
            type="comment",
            tweet_id=tweet_id,
            comment_id=comment.id,
        )

    db.commit()
    db.refresh(comment)

    return comment, author


def list_comments_by_tweet(
    db: Session,
    tweet_id: int,
    limit: int = 20,
    current_user_id: int | None = None,
) -> list[tuple[Comment, User, int, int, int, bool]]:
    """
    Return recent comments for a tweet with comment authors.

    This is useful in interviews to explain how to avoid N+1 queries:
    fetch comments and users in one joined query instead of querying each
    author separately in a loop.
    """
    tweet = db.get(Tweet, tweet_id)
    if tweet is None:
        raise ValueError("tweet not found")

    like_counts = (
        select(
            CommentLike.comment_id,
            func.count().label("like_count"),
        )
        .group_by(CommentLike.comment_id)
        .subquery()
    )

    reply_counts = (
        select(
            Comment.parent_comment_id.label("comment_id"),
            func.count().label("comment_count"),
        )
        .where(Comment.parent_comment_id.is_not(None))
        .group_by(Comment.parent_comment_id)
        .subquery()
    )

    retweet_counts = (
        select(
            CommentRetweet.comment_id,
            func.count().label("retweet_count"),
        )
        .group_by(CommentRetweet.comment_id)
        .subquery()
    )

    stmt = (
        select(
            Comment,
            User,
            func.coalesce(like_counts.c.like_count, 0),
            func.coalesce(reply_counts.c.comment_count, 0),
            func.coalesce(retweet_counts.c.retweet_count, 0),
        )
        .join(User, User.id == Comment.user_id)
        .outerjoin(like_counts, like_counts.c.comment_id == Comment.id)
        .outerjoin(reply_counts, reply_counts.c.comment_id == Comment.id)
        .outerjoin(retweet_counts, retweet_counts.c.comment_id == Comment.id)
        .where(Comment.tweet_id == tweet_id)
        .order_by(Comment.created_at.asc(), Comment.id.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    liked_comment_ids: set[int] = set()
    if current_user_id is not None:
        comment_ids = [comment.id for comment, *_ in rows]
        if comment_ids:
            liked_comment_ids = {
                comment_id
                for (comment_id,) in db.execute(
                    select(CommentLike.comment_id).where(
                        CommentLike.user_id == current_user_id,
                        CommentLike.comment_id.in_(comment_ids),
                    )
                ).all()
            }

    return [
        (
            comment,
            user,
            int(like_count),
            int(comment_count),
            int(retweet_count),
            comment.id in liked_comment_ids,
        )
        for comment, user, like_count, comment_count, retweet_count in rows
    ]


def list_comment_stats(
    db: Session,
    comment_ids: list[int],
    current_user_id: int,
) -> list[dict]:
    ordered_ids = list(dict.fromkeys(comment_ids))
    if not ordered_ids:
        return []

    like_counts = (
        select(
            CommentLike.comment_id,
            func.count().label("like_count"),
        )
        .where(CommentLike.comment_id.in_(ordered_ids))
        .group_by(CommentLike.comment_id)
        .subquery()
    )

    reply_counts = (
        select(
            Comment.parent_comment_id.label("comment_id"),
            func.count().label("comment_count"),
        )
        .where(Comment.parent_comment_id.in_(ordered_ids))
        .group_by(Comment.parent_comment_id)
        .subquery()
    )

    retweet_counts = (
        select(
            CommentRetweet.comment_id,
            func.count().label("retweet_count"),
        )
        .where(CommentRetweet.comment_id.in_(ordered_ids))
        .group_by(CommentRetweet.comment_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Comment.id,
            func.coalesce(like_counts.c.like_count, 0).label("like_count"),
            func.coalesce(reply_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .outerjoin(like_counts, like_counts.c.comment_id == Comment.id)
        .outerjoin(reply_counts, reply_counts.c.comment_id == Comment.id)
        .outerjoin(retweet_counts, retweet_counts.c.comment_id == Comment.id)
        .where(Comment.id.in_(ordered_ids))
    ).all()

    liked_comment_ids = {
        comment_id
        for (comment_id,) in db.execute(
            select(CommentLike.comment_id).where(
                CommentLike.user_id == current_user_id,
                CommentLike.comment_id.in_(ordered_ids),
            )
        ).all()
    }

    stats_by_id = {
        comment_id: {
            "id": comment_id,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "liked_by_me": comment_id in liked_comment_ids,
        }
        for comment_id, like_count, comment_count, retweet_count in rows
    }
    return [stats_by_id[comment_id] for comment_id in ordered_ids if comment_id in stats_by_id]


def like_comment(db: Session, user_id: int, comment_id: int) -> bool:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise ValueError("comment not found")

    existing = db.scalar(
        select(CommentLike).where(
            CommentLike.user_id == user_id,
            CommentLike.comment_id == comment_id,
        )
    )
    if existing:
        return False

    db.add(CommentLike(user_id=user_id, comment_id=comment_id))
    add_notification(
        db,
        recipient_id=comment.user_id,
        actor_id=user_id,
        type="comment_like",
        tweet_id=comment.tweet_id,
        comment_id=comment_id,
    )
    db.commit()
    return True


def unlike_comment(db: Session, user_id: int, comment_id: int) -> bool:
    existing = db.scalar(
        select(CommentLike).where(
            CommentLike.user_id == user_id,
            CommentLike.comment_id == comment_id,
        )
    )
    if existing is None:
        return False

    db.delete(existing)
    db.commit()
    return True


def count_comment_likes(db: Session, comment_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(CommentLike)
            .where(CommentLike.comment_id == comment_id)
        )
        or 0
    )


def has_liked_comment(db: Session, user_id: int, comment_id: int) -> bool:
    return (
        db.scalar(
            select(CommentLike).where(
                CommentLike.user_id == user_id,
                CommentLike.comment_id == comment_id,
            )
        )
        is not None
    )


def retweet_comment(db: Session, user_id: int, comment_id: int) -> bool:
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise ValueError("comment not found")

    existing = db.scalar(
        select(CommentRetweet).where(
            CommentRetweet.user_id == user_id,
            CommentRetweet.comment_id == comment_id,
        )
    )
    if existing:
        return False

    db.add(CommentRetweet(user_id=user_id, comment_id=comment_id))
    add_notification(
        db,
        recipient_id=comment.user_id,
        actor_id=user_id,
        type="comment_retweet",
        tweet_id=comment.tweet_id,
        comment_id=comment_id,
    )
    db.commit()
    return True


def unretweet_comment(db: Session, user_id: int, comment_id: int) -> bool:
    existing = db.scalar(
        select(CommentRetweet).where(
            CommentRetweet.user_id == user_id,
            CommentRetweet.comment_id == comment_id,
        )
    )
    if existing is None:
        return False

    db.delete(existing)
    db.commit()
    return True


def _load_reply_rows_by_ids(db: Session, comment_ids: list[int]) -> dict[int, dict]:
    """
    Load comments (with their author + parent tweet + tweet author) by id.

    Comments whose parent tweet no longer exists are dropped by the inner
    join, so deleted-tweet replies never surface.
    """
    if not comment_ids:
        return {}

    CommentAuthor = aliased(User)
    TweetAuthor = aliased(User)
    rows = db.execute(
        select(Comment, Tweet, CommentAuthor, TweetAuthor)
        .join(Tweet, Tweet.id == Comment.tweet_id)
        .join(CommentAuthor, CommentAuthor.id == Comment.user_id)
        .join(TweetAuthor, TweetAuthor.id == Tweet.user_id)
        .where(Comment.id.in_(comment_ids))
    ).all()

    return {
        comment.id: {
            "comment": comment,
            "comment_author": comment_author,
            "tweet": tweet,
            "tweet_author": tweet_author,
        }
        for comment, tweet, comment_author, tweet_author in rows
    }


def list_replies_by_user(
    db: Session,
    user_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return the user's reply feed newest-first: comments they authored plus
    comments they retweeted, each joined with its parent tweet and authors.

    Ordered by "activity time" — the comment's creation time for an authored
    reply, or the retweet time for a retweeted comment — so a retweet surfaces
    when it was retweeted (Twitter behaviour). A comment that appears both ways
    is deduped to its most recent activity. Retweeted rows carry
    ``"retweeted_by"`` (the retweeting :class:`User`). Fetches limit + 1 rows
    for has-next detection.
    """
    own = select(
        Comment.id.label("comment_id"),
        Comment.created_at.label("activity_at"),
        literal(None).label("retweeter_id"),
    ).where(Comment.user_id == user_id)

    retweeted = select(
        CommentRetweet.comment_id.label("comment_id"),
        CommentRetweet.created_at.label("activity_at"),
        CommentRetweet.user_id.label("retweeter_id"),
    ).where(CommentRetweet.user_id == user_id)

    combined = union_all(own, retweeted).subquery()

    ranked = select(
        combined.c.comment_id,
        combined.c.activity_at,
        combined.c.retweeter_id,
        func.row_number()
        .over(
            partition_by=combined.c.comment_id,
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
        .order_by(ranked.c.activity_at.desc(), ranked.c.comment_id.desc())
        .limit(limit + 1)
    )

    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                ranked.c.activity_at < cursor_created_at,
                and_(
                    ranked.c.activity_at == cursor_created_at,
                    ranked.c.comment_id < cursor_id,
                ),
            )
        )

    ident_rows = db.execute(stmt).all()
    reply_rows = _load_reply_rows_by_ids(
        db, [row.comment_id for row in ident_rows]
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
        base = reply_rows.get(row.comment_id)
        if base is None:
            continue
        feed.append(
            {
                **base,
                "retweeted_by": retweeters_by_id.get(row.retweeter_id)
                if row.retweeter_id is not None
                else None,
                "cursor_created_at": row.activity_at,
                "cursor_id": row.comment_id,
            }
        )
    return feed
