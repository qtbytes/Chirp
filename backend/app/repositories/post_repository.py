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
from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.post_mention import PostMention


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def update_post(
    db: Session,
    post_id: int,
    user_id: int,
    content: str,
    media_urls: list[str] | None,
) -> Post:
    """
    Edit a post's content/media. Only the author may edit.

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


def delete_post(db: Session, post_id: int, user_id: int) -> None:
    """
    Delete a post and its whole reply subtree. Only the author may delete.

    Also removes the engagement (likes), fan-out feed rows, and notifications
    that reference any deleted post, since SQLite here does not enforce foreign
    keys and would otherwise leave orphaned rows. Quote posts that referenced a
    deleted post keep their dangling ``quoted_post_id`` and simply render
    without an embed.
    """
    post = db.get(Post, post_id)
    if post is None:
        raise ValueError("post not found")
    if post.user_id != user_id:
        raise PermissionError("not the author")

    ids = _collect_thread_ids(db, post_id)
    db.execute(delete(Like).where(Like.post_id.in_(ids)))
    db.execute(delete(FeedItem).where(FeedItem.post_id.in_(ids)))
    db.execute(delete(Notification).where(Notification.post_id.in_(ids)))
    db.execute(delete(PostHashtag).where(PostHashtag.post_id.in_(ids)))
    db.execute(delete(PostMention).where(PostMention.post_id.in_(ids)))
    db.execute(delete(Post).where(Post.id.in_(ids)))
    db.commit()
