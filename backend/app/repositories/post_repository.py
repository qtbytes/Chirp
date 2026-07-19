"""
Shared edit/delete operations for posts (tweets and comments are both posts).

Editing and deleting are the same operation regardless of whether the target is
a top-level tweet or a reply, so they live here rather than being duplicated in
the tweet/comment repositories. Both enforce that the caller owns the post.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.models.feed import FeedItem
from app.models.like import Like
from app.models.notification import Notification
from app.models.post import VISIBILITY_VALUES, Post
from app.models.post_hashtag import PostHashtag
from app.models.post_mention import PostMention
from app.models.post_view import PostView
from app.models.report import Report


class TakenDownError(Exception):
    """The post is under a moderation takedown and may not be deleted."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def update_post(
    db: Session,
    post_id: int,
    user_id: int,
    content: str,
    media_urls: list[str] | None,
    media_alts: list[str] | None = None,
    visibility: str | None = None,
) -> Post:
    """
    Edit a post's content/media. Only the author may edit.

    ``visibility`` changes the audience of a *top-level tweet*; ``None`` leaves it
    unchanged (a plain content edit must not silently reset a restricted tweet to
    public), and it is ignored for replies, which have no audience of their own.
    An unrecognised value is ignored rather than stored.

    Read paths gate on the current value, so no fan-out cleanup is needed when the
    audience narrows or widens -- the change simply takes effect on the next read.

    Raises ValueError("post not found") if missing and PermissionError if the
    caller is not the author. Stamps ``edited_at`` and returns the post with its
    author loaded.
    """
    post = db.get(Post, post_id)
    if post is None:
        raise ValueError("post not found")
    if post.user_id != user_id:
        raise PermissionError("not the author")

    post.content = content
    post.media_urls = media_urls or None
    # All-empty alt lists store as NULL, not [""] * n.
    post.media_alts = media_alts if media_alts and any(media_alts) else None
    if (
        visibility is not None
        and post.reply_to_id is None
        and visibility in VISIBILITY_VALUES
    ):
        post.visibility = visibility
    post.edited_at = _utcnow()

    # Re-sync the post's #hashtags / @mentions against the edited text (and notify
    # any newly mentioned user). Imported lazily to avoid an import cycle.
    from app.repositories import entity_repository

    entity_repository.sync_post_entities(db, post, user_id)

    db.commit()

    return db.scalar(
        select(Post).options(joinedload(Post.author)).where(Post.id == post_id)
    )


def _collect_thread_ids(db: Session, post_id: int) -> list[int]:
    """
    Return the post plus every descendant reply id (the whole subtree).

    Replies are chained via ``reply_to_id``; a breadth-first walk collects the
    post and all posts that (transitively) reply to it, so deleting a tweet
    removes its whole thread and deleting a comment removes its sub-thread.
    """
    collected = [post_id]
    frontier = [post_id]
    while frontier:
        children = [
            child_id
            for (child_id,) in db.execute(
                select(Post.id).where(Post.reply_to_id.in_(frontier))
            ).all()
        ]
        new_ids = [cid for cid in children if cid not in collected]
        collected.extend(new_ids)
        frontier = new_ids
    return collected


def delete_post(db: Session, post_id: int, user_id: int) -> list[str]:
    """
    Delete a post and its whole reply subtree. Only the author may delete.
    Returns the media URLs the deleted posts carried, so the route can clean
    up the files behind them (a filesystem concern that stays out of here).

    A post under a moderation takedown is *not* deletable (``TakenDownError``):
    the surviving row is the moderation record -- the evidence behind the
    judgement and the thing a restore would bring back -- and the one person
    who must not be able to destroy that record is its author. Taken-down
    *descendants* swept up by deleting an ancestor are still removed: refusing
    would let someone else's bad reply hold the author's own post hostage.

    Also removes the engagement (likes, views), fan-out feed rows,
    notifications, and reports that reference any deleted post, since SQLite
    here does not enforce foreign keys and would otherwise leave orphaned
    rows. Discarding the reports is deliberate, not just hygiene: with the
    post gone there is nothing left to judge -- the author has done what a
    takedown would have -- and a report row whose target is missing could
    never be shown or resolved, only haunt the queue. Quote posts that
    referenced a deleted post keep their dangling ``quoted_post_id`` and
    simply render without an embed.
    """
    post = db.get(Post, post_id)
    if post is None:
        raise ValueError("post not found")
    if post.user_id != user_id:
        raise PermissionError("not the author")
    if post.is_taken_down:
        raise TakenDownError("post is under a moderation takedown")

    ids = _collect_thread_ids(db, post_id)
    # Filtered in Python: the JSON column stores absent media as JSON 'null',
    # which an SQL IS NOT NULL happily passes through as a Python None.
    media_urls = [
        url
        for (urls,) in db.execute(select(Post.media_urls).where(Post.id.in_(ids)))
        if urls
        for url in urls
    ]
    db.execute(delete(Like).where(Like.post_id.in_(ids)))
    db.execute(delete(PostView).where(PostView.post_id.in_(ids)))
    db.execute(delete(FeedItem).where(FeedItem.post_id.in_(ids)))
    db.execute(delete(Notification).where(Notification.post_id.in_(ids)))
    db.execute(delete(PostHashtag).where(PostHashtag.post_id.in_(ids)))
    db.execute(delete(PostMention).where(PostMention.post_id.in_(ids)))
    db.execute(delete(Report).where(Report.post_id.in_(ids)))
    db.execute(delete(Post).where(Post.id.in_(ids)))
    db.commit()
    return media_urls
