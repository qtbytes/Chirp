"""
Persist the ``#hashtag`` / ``@mention`` entities of a post, and notify mentioned
users.

Called from every post write path (create tweet, create comment, edit) inside
the caller's transaction -- it only stages rows and notifications, the caller's
existing ``commit`` persists them. Re-running it for the same post is how an edit
re-syncs: the post's old entity rows are cleared and rebuilt from the new text.
"""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.post import Post
from app.models.post_hashtag import PostHashtag
from app.models.post_mention import PostMention
from app.repositories import notification_repository, user_repository
from app.repositories.block_repository import blocks_between
from app.services.text_entities import extract_hashtags, extract_mention_usernames


def sync_post_entities(db: Session, post: Post, author_id: int) -> None:
    """
    Rebuild ``post_hashtags`` / ``post_mentions`` for ``post`` from its current
    content and notify newly mentioned users.

    Mentions resolve to a user only when the username exists, is not deleted, is
    not the author themselves, and there is no block in either direction. The
    ``mention`` notification collapses on (recipient, actor, type, post_id) in
    ``add_notification``, so editing a post to re-mention the same person does not
    stack a second notification, while a freshly added mention still fires.
    """
    # Clear the post's previous entities so an edit does not leave stale rows.
    db.execute(delete(PostHashtag).where(PostHashtag.post_id == post.id))
    db.execute(delete(PostMention).where(PostMention.post_id == post.id))

    for tag in extract_hashtags(post.content):
        db.add(PostHashtag(post_id=post.id, tag=tag))

    for username in extract_mention_usernames(post.content):
        user = user_repository.get_user_by_username(db, username)
        if user is None or user.deleted_at is not None or user.id == author_id:
            continue
        if blocks_between(db, author_id, user.id):
            continue

        db.add(PostMention(post_id=post.id, mentioned_user_id=user.id))
        notification_repository.add_notification(
            db,
            recipient_id=user.id,
            actor_id=author_id,
            type="mention",
            post_id=post.id,
        )
