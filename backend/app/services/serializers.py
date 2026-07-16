"""Small shared helpers to turn ORM posts into API schema objects."""

from app.models.post import Post
from app.schemas.tweet import QuotedPostOut
from app.schemas.user import UserSummary


def serialize_quoted_post(quoted: Post | None) -> QuotedPostOut | None:
    """
    Build the compact embedded view of a quoted post.

    Returns ``None`` when there is no quote or the quoted post has been deleted
    (a dangling ``quoted_post_id``), so callers can simply pass
    ``post.quoted_post`` through.
    """
    if quoted is None:
        return None
    return QuotedPostOut(
        id=quoted.id,
        content=quoted.content,
        media_urls=quoted.media_urls or [],
        media_alts=quoted.media_alts or [],
        created_at=quoted.created_at,
        author=UserSummary.model_validate(quoted.author),
    )
