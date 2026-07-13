from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.like import Like
from app.models.post import Post
from app.models.user import User
from app.repositories.block_repository import blocks_between
from app.repositories.notification_repository import add_notification
from app.repositories.tweet_repository import _quote_counts_subquery
from app.repositories.visibility import can_view_thread, visible_root_predicate


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

    # A blocked user must not be able to like the blocker's post, nor vice versa.
    # Report it as "not found" so a block is not disclosed by a distinct error.
    if blocks_between(db, user_id, post.user_id):
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

    # No commenting across a block, in either direction -- reported as a missing
    # thread rather than a distinct "blocked" error.
    if blocks_between(db, user_id, tweet.user_id):
        raise ValueError("tweet not found")

    # You cannot reply to a thread you are not allowed to see (a followers-only or
    # private tweet) -- also reported as missing.
    if not can_view_thread(db, user_id, tweet):
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
        # Also honour a block against the specific comment's author.
        if blocks_between(db, user_id, parent_comment.user_id):
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

    # Index the reply's #hashtags / @mentions and notify anyone mentioned.
    # Imported lazily to avoid a repositories-package import cycle.
    from app.repositories import entity_repository

    entity_repository.sync_post_entities(db, post, user_id)

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
    exclude_author_ids: set[int] | None = None,
) -> list[tuple[Post, User, int, int, int, bool]]:
    """
    Return a tweet's whole-thread comments as a nested thread.

    Comments are ordered depth-first (pre-order): each reply immediately follows
    the comment it replies to, and siblings stay in creation order. This lets the
    UI indent each comment under its parent instead of showing a flat, purely
    chronological list.

    ``exclude_author_ids`` removes comments by blocked (or blocking) authors. A
    hidden author's comment is dropped *with its subtree*: the pre-order walk
    starts from the tweet's replies, so a reply whose parent was removed is never
    reached, which is the desired "hide the whole sub-conversation" behaviour.
    """
    tweet = db.get(Post, tweet_id)
    if tweet is None or tweet.reply_to_id is not None:
        raise ValueError("tweet not found")

    # The thread is only readable if the viewer may see its tweet; a
    # followers-only or private tweet's replies stay hidden (reported as missing).
    if not can_view_thread(db, current_user_id, tweet):
        raise ValueError("tweet not found")

    like_counts = (
        select(Like.post_id, func.count().label("like_count"))
        .group_by(Like.post_id)
        .subquery()
    )
    retweet_counts = _quote_counts_subquery()
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
    )
    if exclude_author_ids:
        stmt = stmt.where(Post.user_id.not_in(exclude_author_ids))
    rows = db.execute(stmt).all()

    # Group each comment under its parent (reply_to_id); a top-level comment's
    # parent is the tweet itself. Siblings keep the SQL creation order.
    rows_by_id: dict[int, tuple] = {}
    children_by_parent: dict[int, list[int]] = {}
    for row in rows:
        post = row[0]
        rows_by_id[post.id] = row
        children_by_parent.setdefault(post.reply_to_id, []).append(post.id)

    # Depth-first pre-order walk starting from the tweet's direct replies.
    ordered: list[tuple] = []
    stack = list(reversed(children_by_parent.get(tweet_id, [])))
    while stack and len(ordered) < limit:
        comment_id = stack.pop()
        ordered.append(rows_by_id[comment_id])
        for child_id in reversed(children_by_parent.get(comment_id, [])):
            stack.append(child_id)

    liked_ids: set[int] = set()
    if current_user_id is not None:
        comment_ids = [row[0].id for row in ordered]
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
        for post, user, like_count, comment_count, retweet_count in ordered
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
    retweet_counts = _quote_counts_subquery(ordered_ids)

    rows = db.execute(
        select(
            Post.id,
            Post.view_count,
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
            "view_count": int(view_count),
            "liked_by_me": post_id in liked_ids,
        }
        for post_id, view_count, like_count, comment_count, retweet_count in rows
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
    current_user_id: int | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return the user's reply feed newest-first: the replies they authored, each
    joined with its immediate parent and the authors involved.

    A reply is only shown when the *viewer* (``current_user_id``) may see its
    thread root, so a reply into a followers-only or private tweet does not leak
    that thread to a stranger browsing the author's replies.
    """
    Root = aliased(Post)
    stmt = (
        select(Post.id, Post.created_at)
        .join(Root, Root.id == func.coalesce(Post.root_id, Post.id))
        .where(
            Post.user_id == user_id,
            Post.reply_to_id.is_not(None),
            visible_root_predicate(current_user_id, Root),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit + 1)
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

    ident_rows = db.execute(stmt).all()
    reply_rows = _load_reply_rows_by_ids(db, [row_id for row_id, _ in ident_rows])

    feed: list[dict] = []
    for row_id, created_at in ident_rows:
        base = reply_rows.get(row_id)
        if base is None:
            continue
        feed.append(
            {
                **base,
                "cursor_created_at": created_at,
                "cursor_id": row_id,
            }
        )
    return feed
