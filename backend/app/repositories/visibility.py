"""
Per-tweet audience visibility.

A top-level tweet carries a ``visibility`` -- ``public`` (everyone), ``followers``
(the author and the people who follow them), or ``private`` (the author alone).
Replies have no audience of their own; a whole thread is governed by the
visibility of its *root* tweet, so every read gates on the root.

The rule, for a viewer ``V`` and a post whose thread root was written by author
``A`` with visibility ``Vis``:

- ``V`` may always see their own posts (``V == A``),
- ``public``: everyone,
- ``followers``: only if ``V`` follows ``A``,
- ``private``: only ``A`` (covered by the first clause).

``visible_root_predicate`` is the SQL form, applied to every read path -- the
timelines, profile, single tweet, thread, search, hashtag feed and the
notification list -- alongside the existing block / deleted-author filters.
``can_view_post`` / ``can_view_thread`` are the row-at-a-time form for the
single-object paths (a fetched tweet, a quoted embed).
"""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.follow import Follow
from app.models.post import Post


def visible_root_predicate(viewer_id: int | None, root=Post):
    """
    SQL condition selecting posts whose thread root ``root`` is visible to
    ``viewer_id``.

    ``root`` is the ``Post`` class for queries already scoped to top-level tweets
    (a tweet is its own root); pass an ``aliased(Post)`` joined on ``root_id`` for
    queries that also include replies (search), so the gate reads the root's
    audience rather than the reply's own default.

    A missing viewer (``None``) sees only public posts.
    """
    if viewer_id is None:
        return root.visibility == "public"

    followee_ids = select(Follow.followee_id).where(Follow.follower_id == viewer_id)
    return or_(
        root.user_id == viewer_id,
        root.visibility == "public",
        and_(root.visibility == "followers", root.user_id.in_(followee_ids)),
    )


def can_view_post(db: Session, viewer_id: int | None, post: Post | None) -> bool:
    """
    Whether ``viewer_id`` may see ``post`` by *its own* visibility.

    Used for a quoted embed, which is a top-level tweet (its own root), so its own
    ``visibility`` is the audience gate. Only ``followers`` visibility touches the
    database; ``public`` / own / ``private`` decide in memory.
    """
    if post is None:
        return False
    if post.visibility == "public":
        return True
    if viewer_id is not None and post.user_id == viewer_id:
        return True
    if post.visibility == "followers" and viewer_id is not None:
        return (
            db.scalar(
                select(Follow.follower_id).where(
                    Follow.follower_id == viewer_id,
                    Follow.followee_id == post.user_id,
                )
            )
            is not None
        )
    return False


def can_view_thread(db: Session, viewer_id: int | None, post: Post | None) -> bool:
    """
    Whether ``viewer_id`` may see the thread ``post`` belongs to.

    Resolves ``post`` to its root tweet (itself if top-level) and gates on the
    root's audience -- so a reply is visible exactly when its root tweet is.
    """
    if post is None:
        return False
    root = post if post.reply_to_id is None else db.get(Post, post.root_id or post.id)
    return can_view_post(db, viewer_id, root)
