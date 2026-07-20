from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.follow import Follow
from app.models.like import Like
from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.post import DEFAULT_VISIBILITY, VISIBILITY_VALUES
from app.models.user import User
from app.repositories.block_repository import blocks_between
from app.repositories.notification_repository import add_notification
from app.repositories.visibility import can_view_thread, visible_root_predicate


def deleted_author_ids():
    """
    A subquery of the ids of deleted (tombstoned) accounts, for use with
    ``Post.user_id.not_in(...)``. Feeds filter these out so a deleted account's
    posts stop surfacing in timelines and discovery -- but a profile still shows
    them, because that path deliberately does not apply this filter.
    """
    return select(User.id).where(User.deleted_at.is_not(None))


def _quote_counts_subquery(post_ids: list[int] | None = None):
    """
    Subquery counting how many quote posts reference each post.

    A "retweet" is now a quote post (a post whose ``quoted_post_id`` points at
    the original), so the retweet count is simply the number of such posts.
    """
    stmt = select(
        Post.quoted_post_id.label("post_id"),
        func.count().label("retweet_count"),
    ).where(Post.quoted_post_id.is_not(None))
    if post_ids is not None:
        stmt = stmt.where(Post.quoted_post_id.in_(post_ids))
    return stmt.group_by(Post.quoted_post_id).subquery()


def create_tweet(
    db: Session,
    author_id: int,
    content: str,
    media_urls: list[str] | None = None,
    media_alts: list[str] | None = None,
    quoted_post_id: int | None = None,
    visibility: str | None = DEFAULT_VISIBILITY,
) -> Post | None:
    """
    Create a top-level post (tweet) and reload it with author information.

    A top-level post is its own thread root, so ``root_id`` is set to the post's
    own id after it is assigned. When ``quoted_post_id`` is given the post is a
    quote (Twitter-style repost); the quoted author is notified.

    ``visibility`` is the tweet's audience (``public`` / ``followers`` /
    ``private``); an unknown value falls back to ``public`` rather than storing an
    unenforceable audience. A ``private`` tweet is seen only by its author, so it
    generates no outbound notifications.

    Raises ``ValueError`` if ``quoted_post_id`` refers to a post that does not
    exist or that the author is not allowed to see.
    """
    if visibility not in VISIBILITY_VALUES:
        visibility = DEFAULT_VISIBILITY

    quoted = None
    if quoted_post_id is not None:
        quoted = db.get(Post, quoted_post_id)
        if quoted is None:
            raise ValueError("quoted post not found")
        # You cannot quote across a block; hidden the same way a missing post is.
        if blocks_between(db, author_id, quoted.user_id):
            raise ValueError("quoted post not found")
        # You cannot quote a tweet you are not allowed to see (a followers-only or
        # private thread) -- also reported as "not found".
        if not can_view_thread(db, author_id, quoted):
            raise ValueError("quoted post not found")

    post = Post(
        user_id=author_id,
        content=content,
        media_urls=media_urls or None,
        # All-empty alt lists store as NULL, not [""] * n.
        media_alts=media_alts if media_alts and any(media_alts) else None,
        quoted_post_id=quoted_post_id,
        visibility=visibility,
    )
    db.add(post)
    db.flush()  # assign post.id
    post.root_id = post.id

    if quoted is not None and quoted.user_id != author_id and visibility != "private":
        add_notification(
            db,
            recipient_id=quoted.user_id,
            actor_id=author_id,
            type="retweet",
            post_id=post.id,
        )

    # Index the post's #hashtags / @mentions and notify anyone mentioned, in the
    # same transaction as the post. Imported lazily to avoid an import cycle
    # while the repositories package is still initialising.
    from app.repositories import entity_repository

    entity_repository.sync_post_entities(db, post, author_id)

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
    retweet_counts = _quote_counts_subquery(ordered_ids)

    rows = db.execute(
        select(
            Post.id,
            Post.view_count,
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
            "view_count": int(view_count),
            "liked_by_me": post_id in liked_ids,
        }
        for post_id, view_count, like_count, comment_count, retweet_count in rows
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
    retweet_counts = _quote_counts_subquery(unique_ids)

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
            "view_count": post.view_count,
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
    exclude_author_ids: set[int] | None = None,
    exclude_deleted_authors: bool = False,
    exclude_post_id: int | None = None,
) -> list[dict]:
    """
    Return the given authors' top-level posts newest-first.

    Quotes (Twitter-style reposts) are ordinary top-level posts now, so they
    surface here naturally by their own ``created_at`` — no separate retweet
    join is needed.

    ``exclude_author_ids`` drops posts from blocked (or blocking) authors. A
    block already removes the follow, so a followed-author feed rarely needs it;
    it is defence in depth against a stale edge.

    ``exclude_deleted_authors`` drops posts by tombstoned accounts. It is off by
    default so a profile still lists its owner's posts even once deleted; the
    timeline turns it on.

    ``exclude_post_id`` drops a single post from the results. The profile uses it
    to keep the pinned tweet -- surfaced separately at the top -- from also
    appearing in the chronological list. Excluded in SQL (not after the fetch) so
    the ``limit + 1`` next-page probe stays accurate.
    """
    if not author_ids:
        return []

    ident_stmt = (
        select(Post.id, Post.created_at)
        .where(
            Post.user_id.in_(author_ids),
            Post.reply_to_id.is_(None),
            # These are top-level tweets, so each is its own thread root; gate on
            # the tweet's own audience for the viewer.
            visible_root_predicate(current_user_id),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit + 1)
    )
    if exclude_author_ids:
        ident_stmt = ident_stmt.where(Post.user_id.not_in(exclude_author_ids))
    if exclude_post_id is not None:
        ident_stmt = ident_stmt.where(Post.id != exclude_post_id)
    if exclude_deleted_authors:
        ident_stmt = ident_stmt.where(Post.user_id.not_in(deleted_author_ids()))
    if cursor_created_at is not None and cursor_id is not None:
        ident_stmt = ident_stmt.where(
            or_(
                Post.created_at < cursor_created_at,
                and_(
                    Post.created_at == cursor_created_at,
                    Post.id < cursor_id,
                ),
            )
        )

    ident_rows = db.execute(ident_stmt).all()
    tweet_rows = _load_tweet_rows_by_ids(
        db, [row_id for row_id, _ in ident_rows], current_user_id
    )

    feed: list[dict] = []
    for row_id, created_at in ident_rows:
        base = tweet_rows.get(row_id)
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


def list_tweets_by_hashtag(
    db: Session,
    tag: str,
    limit: int,
    current_user_id: int | None = None,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
    exclude_author_ids: set[int] | None = None,
    exclude_deleted_authors: bool = True,
) -> list[dict]:
    """
    Return top-level posts carrying an exact ``#tag``, newest-first.

    This is the hashtag feed. Unlike ``/search`` (an FTS text match that strips
    the ``#`` and prefix-matches the word), membership is the structured
    ``post_hashtags`` rows the write path recorded, so only posts that actually
    used the tag appear. Same ``(created_at, id)`` cursor and visibility filters
    as ``list_feed_with_retweets``; ``tag`` must already be normalised (lowercase,
    no leading ``#``) to match how tags are stored.
    """
    ident_stmt = (
        select(Post.id, Post.created_at)
        .join(PostHashtag, PostHashtag.post_id == Post.id)
        .where(
            PostHashtag.tag == tag,
            Post.reply_to_id.is_(None),
            visible_root_predicate(current_user_id),
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit + 1)
    )
    if exclude_author_ids:
        ident_stmt = ident_stmt.where(Post.user_id.not_in(exclude_author_ids))
    if exclude_deleted_authors:
        ident_stmt = ident_stmt.where(Post.user_id.not_in(deleted_author_ids()))
    if cursor_created_at is not None and cursor_id is not None:
        ident_stmt = ident_stmt.where(
            or_(
                Post.created_at < cursor_created_at,
                and_(
                    Post.created_at == cursor_created_at,
                    Post.id < cursor_id,
                ),
            )
        )

    ident_rows = db.execute(ident_stmt).all()
    tweet_rows = _load_tweet_rows_by_ids(
        db, [row_id for row_id, _ in ident_rows], current_user_id
    )

    feed: list[dict] = []
    for row_id, created_at in ident_rows:
        base = tweet_rows.get(row_id)
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


def fetch_for_you_candidates(
    db: Session,
    limit: int,
    current_user_id: int | None = None,
    exclude_author_ids: set[int] | None = None,
    exclude_deleted_authors: bool = False,
    tag: str | None = None,
) -> list[dict]:
    """
    A bounded pool of recent top-level posts, with the engagement and
    viewer-affinity signals the "for you" ranker needs.

    Recency selects the *pool* -- the ``limit`` newest top-level posts -- which
    bounds the read-time work (the old query scanned every post ever written).
    The ranking itself happens in the service, over these candidates. Each row
    carries, for the viewer:

    - ``like_count`` / ``comment_count`` / ``retweet_count`` -- the post's
      engagement (``view_count`` rides on the Post row itself),
    - ``follows_author`` -- whether the viewer follows the author,
    - ``viewer_like_affinity`` -- how many of that author's posts the viewer has
      liked (an affinity signal, capped later by the scorer).

    ``tag`` restricts the pool to posts carrying that hashtag (normalised,
    no leading ``#``) -- the hashtag feed's "Top" sort reuses this ranker over
    a tag-scoped pool.
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
    retweet_counts = _quote_counts_subquery()

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
    if tag is not None:
        stmt = stmt.join(PostHashtag, PostHashtag.post_id == Post.id).where(
            PostHashtag.tag == tag
        )
    # Filter hidden authors and audience-restricted tweets out of the pool
    # *before* the limit, so a blocked/deleted author -- or a followers-only tweet
    # the viewer can't see -- cannot consume candidate slots the viewer should
    # have seen.
    if exclude_author_ids:
        stmt = stmt.where(Post.user_id.not_in(exclude_author_ids))
    if exclude_deleted_authors:
        stmt = stmt.where(Post.user_id.not_in(deleted_author_ids()))
    stmt = stmt.where(visible_root_predicate(current_user_id))
    stmt = stmt.order_by(Post.created_at.desc(), Post.id.desc()).limit(limit)

    rows = db.execute(stmt).all()
    posts = [post for post, *_ in rows]
    post_ids = [post.id for post in posts]
    author_ids = {post.user_id for post in posts}

    liked_ids: set[int] = set()
    followed_authors: set[int] = set()
    likes_on_author: dict[int, int] = {}
    if current_user_id is not None and post_ids:
        liked_ids = {
            post_id
            for (post_id,) in db.execute(
                select(Like.post_id).where(
                    Like.user_id == current_user_id,
                    Like.post_id.in_(post_ids),
                )
            ).all()
        }
        followed_authors = {
            followee_id
            for (followee_id,) in db.execute(
                select(Follow.followee_id).where(
                    Follow.follower_id == current_user_id,
                    Follow.followee_id.in_(author_ids),
                )
            ).all()
        }
        # How many of each candidate author's posts the viewer has liked, ever --
        # a durable "I engage with this person" signal, not just on this pool.
        likes_on_author = {
            author_id: int(count)
            for author_id, count in db.execute(
                select(Post.user_id, func.count())
                .join(Like, Like.post_id == Post.id)
                .where(
                    Like.user_id == current_user_id,
                    Post.user_id.in_(author_ids),
                )
                .group_by(Post.user_id)
            ).all()
        }

    return [
        {
            "tweet": post,
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "retweet_count": int(retweet_count),
            "view_count": post.view_count,
            "liked_by_me": post.id in liked_ids,
            "follows_author": post.user_id in followed_authors,
            "viewer_like_affinity": likes_on_author.get(post.user_id, 0),
        }
        for post, like_count, comment_count, retweet_count in rows
    ]


def count_posts_by_author(db: Session, author_id: int) -> int:
    """
    Everything the user has authored: top-level tweets, replies, and quotes
    (a retweet is a quote post here, so it counts too). Matches the profile
    counter semantics of Twitter and Bluesky, both of which count replies.
    """
    return int(
        db.scalar(
            select(func.count()).select_from(Post).where(Post.user_id == author_id)
        )
        or 0
    )
