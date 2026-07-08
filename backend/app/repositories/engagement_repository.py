from datetime import datetime

from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from app.models.like import Like
from app.models.post import Post
from app.models.retweet import Retweet
from app.models.user import User
from app.repositories.notification_repository import add_notification


# --- likes -----------------------------------------------------------------
#
# Tweets and comments are both posts now, so a "like a tweet" and a "like a
# comment" are the same operation on the shared ``likes`` table. The tweet_*
# and comment_* names are kept as thin wrappers so the two route modules stay
# unchanged.


def _like_post(db: Session, user_id: int, post_id: int) -> bool:
    post = db.get(Post, post_id)
    if post is None:
        raise ValueError("post not found")

    existing = db.scalar(
        select(Like).where(Like.user_id == user_id, Like.post_id == post_id)
    )
    if existing:
        return False

    db.add(Like(user_id=user_id, post_id=post_id))
    add_notification(
        db,
        recipient_id=post.user_id,
        actor_id=user_id,
        type="like",
        post_id=post_id,
    )
    db.commit()
    return True


def _unlike_post(db: Session, user_id: int, post_id: int) -> bool:
    existing = db.scalar(
        select(Like).where(Like.user_id == user_id, Like.post_id == post_id)
    )
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True


def _count_post_likes(db: Session, post_id: int) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Like).where(Like.post_id == post_id))
        or 0
    )


def _has_liked_post(db: Session, user_id: int, post_id: int) -> bool:
    return (
        db.scalar(
            select(Like).where(Like.user_id == user_id, Like.post_id == post_id)
        )
        is not None
    )


def like_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    try:
        return _like_post(db, user_id, tweet_id)
    except ValueError as exc:
        raise ValueError("tweet not found") from exc


def unlike_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    return _unlike_post(db, user_id, tweet_id)


def count_tweet_likes(db: Session, tweet_id: int) -> int:
    return _count_post_likes(db, tweet_id)


def has_liked_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    return _has_liked_post(db, user_id, tweet_id)


def like_comment(db: Session, user_id: int, comment_id: int) -> bool:
    try:
        return _like_post(db, user_id, comment_id)
    except ValueError as exc:
        raise ValueError("comment not found") from exc


def unlike_comment(db: Session, user_id: int, comment_id: int) -> bool:
    return _unlike_post(db, user_id, comment_id)


def count_comment_likes(db: Session, comment_id: int) -> int:
    return _count_post_likes(db, comment_id)


def has_liked_comment(db: Session, user_id: int, comment_id: int) -> bool:
    return _has_liked_post(db, user_id, comment_id)


# --- retweets --------------------------------------------------------------


def _retweet_post(db: Session, user_id: int, post_id: int) -> bool:
    post = db.get(Post, post_id)
    if post is None:
        raise ValueError("post not found")

    existing = db.scalar(
        select(Retweet).where(Retweet.user_id == user_id, Retweet.post_id == post_id)
    )
    if existing:
        return False

    db.add(Retweet(user_id=user_id, post_id=post_id))
    add_notification(
        db,
        recipient_id=post.user_id,
        actor_id=user_id,
        type="retweet",
        post_id=post_id,
    )
    db.commit()
    return True


def _unretweet_post(db: Session, user_id: int, post_id: int) -> bool:
    existing = db.scalar(
        select(Retweet).where(Retweet.user_id == user_id, Retweet.post_id == post_id)
    )
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    return True


def retweet_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    try:
        return _retweet_post(db, user_id, tweet_id)
    except ValueError as exc:
        raise ValueError("tweet not found") from exc


def unretweet_tweet(db: Session, user_id: int, tweet_id: int) -> bool:
    return _unretweet_post(db, user_id, tweet_id)


def retweet_comment(db: Session, user_id: int, comment_id: int) -> bool:
    try:
        return _retweet_post(db, user_id, comment_id)
    except ValueError as exc:
        raise ValueError("comment not found") from exc


def unretweet_comment(db: Session, user_id: int, comment_id: int) -> bool:
    return _unretweet_post(db, user_id, comment_id)


# --- comments / replies ----------------------------------------------------


def create_comment(
    db: Session,
    user_id: int,
    tweet_id: int,
    content: str,
    parent_comment_id: int | None = None,
    media_urls: list[str] | None = None,
) -> tuple[Post, User]:
    """
    Create a reply post under a tweet (optionally under a parent comment) and
    return the post with its author.

    The new post's ``root_id`` is always the thread's origin tweet; its
    ``reply_to_id`` is the tweet (top-level comment) or the parent comment.
    """
    tweet = db.get(Post, tweet_id)
    if tweet is None or tweet.reply_to_id is not None:
        raise ValueError("tweet not found")

    author = db.get(User, user_id)
    if author is None:
        raise ValueError("user not found")

    parent_comment = None
    reply_to_id = tweet_id
    if parent_comment_id is not None:
        parent_comment = db.get(Post, parent_comment_id)
        if parent_comment is None or parent_comment.root_id != tweet_id:
            raise ValueError("parent comment not found")
        reply_to_id = parent_comment_id

    post = Post(
        user_id=user_id,
        content=content,
        media_urls=media_urls or None,
        reply_to_id=reply_to_id,
        root_id=tweet_id,
    )
    db.add(post)
    db.flush()  # assign post.id before staging the notification

    if parent_comment is not None:
        add_notification(
            db,
            recipient_id=parent_comment.user_id,
            actor_id=user_id,
            type="reply",
            post_id=post.id,
        )
    else:
        add_notification(
            db,
            recipient_id=tweet.user_id,
            actor_id=user_id,
            type="comment",
            post_id=post.id,
        )

    db.commit()
    db.refresh(post)
    return post, author


def _direct_reply_counts(comment_ids: list[int]):
    """Subquery: number of direct replies to each comment (reply_to_id match)."""
    return (
        select(
            Post.reply_to_id.label("comment_id"),
            func.count().label("comment_count"),
        )
        .where(Post.reply_to_id.in_(comment_ids))
        .group_by(Post.reply_to_id)
        .subquery()
    )


def list_comments_by_tweet(
    db: Session,
    tweet_id: int,
    limit: int = 20,
    current_user_id: int | None = None,
) -> list[tuple[Post, User, int, int, int, bool]]:
    """Return a tweet's whole-thread comments (oldest first) with authors."""
    tweet = db.get(Post, tweet_id)
    if tweet is None or tweet.reply_to_id is not None:
        raise ValueError("tweet not found")

    like_counts = (
        select(Like.post_id, func.count().label("like_count"))
        .group_by(Like.post_id)
        .subquery()
    )
    retweet_counts = (
        select(Retweet.post_id, func.count().label("retweet_count"))
        .group_by(Retweet.post_id)
        .subquery()
    )
    reply_counts = (
        select(
            Post.reply_to_id.label("comment_id"),
            func.count().label("comment_count"),
        )
        .group_by(Post.reply_to_id)
        .subquery()
    )

    stmt = (
        select(
            Post,
            User,
            func.coalesce(like_counts.c.like_count, 0),
            func.coalesce(reply_counts.c.comment_count, 0),
            func.coalesce(retweet_counts.c.retweet_count, 0),
        )
        .join(User, User.id == Post.user_id)
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(reply_counts, reply_counts.c.comment_id == Post.id)
        .outerjoin(retweet_counts, retweet_counts.c.post_id == Post.id)
        .where(Post.root_id == tweet_id, Post.id != tweet_id)
        .order_by(Post.created_at.asc(), Post.id.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    liked_ids: set[int] = set()
    if current_user_id is not None:
        comment_ids = [post.id for post, *_ in rows]
        if comment_ids:
            liked_ids = {
                post_id
                for (post_id,) in db.execute(
                    select(Like.post_id).where(
                        Like.user_id == current_user_id,
                        Like.post_id.in_(comment_ids),
                    )
                ).all()
            }

    return [
        (
            post,
            user,
            int(like_count),
            int(comment_count),
            int(retweet_count),
            post.id in liked_ids,
        )
        for post, user, like_count, comment_count, retweet_count in rows
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
        select(Like.post_id, func.count().label("like_count"))
        .where(Like.post_id.in_(ordered_ids))
        .group_by(Like.post_id)
        .subquery()
    )
    reply_counts = _direct_reply_counts(ordered_ids)
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
            func.coalesce(reply_counts.c.comment_count, 0).label("comment_count"),
            func.coalesce(retweet_counts.c.retweet_count, 0).label("retweet_count"),
        )
        .outerjoin(like_counts, like_counts.c.post_id == Post.id)
        .outerjoin(reply_counts, reply_counts.c.comment_id == Post.id)
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


def _load_reply_rows_by_ids(db: Session, comment_ids: list[int]) -> dict[int, dict]:
    """
    Load reply posts (with author + immediate parent + parent author) by id.

    The parent is the post the reply was made to (``reply_to_id``) — the real
    parent, which may be a top-level tweet or another comment, not necessarily
    the thread root. Replies whose parent no longer exists are dropped by the
    inner join.
    """
    if not comment_ids:
        return {}

    CommentAuthor = aliased(User)
    ParentPost = aliased(Post)
    ParentAuthor = aliased(User)
    rows = db.execute(
        select(Post, ParentPost, CommentAuthor, ParentAuthor)
        .join(ParentPost, ParentPost.id == Post.reply_to_id)
        .join(CommentAuthor, CommentAuthor.id == Post.user_id)
        .join(ParentAuthor, ParentAuthor.id == ParentPost.user_id)
        .where(Post.id.in_(comment_ids))
    ).all()

    return {
        comment.id: {
            "comment": comment,
            "comment_author": comment_author,
            "tweet": parent_post,
            "tweet_author": parent_author,
        }
        for comment, parent_post, comment_author, parent_author in rows
    }


def list_replies_by_user(
    db: Session,
    user_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return the user's reply feed newest-first: replies they authored plus
    replies (comments) they retweeted, ordered by activity time, each joined
    with its root tweet and authors. Retweeted rows carry ``"retweeted_by"``.
    """
    own = select(
        Post.id.label("comment_id"),
        Post.created_at.label("activity_at"),
        literal(None).label("retweeter_id"),
    ).where(Post.user_id == user_id, Post.reply_to_id.is_not(None))

    retweeted = (
        select(
            Retweet.post_id.label("comment_id"),
            Retweet.created_at.label("activity_at"),
            Retweet.user_id.label("retweeter_id"),
        )
        .join(Post, Post.id == Retweet.post_id)
        .where(Retweet.user_id == user_id, Post.reply_to_id.is_not(None))
    )

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
    reply_rows = _load_reply_rows_by_ids(db, [r.comment_id for r in ident_rows])

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
