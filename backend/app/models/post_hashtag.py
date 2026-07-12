from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PostHashtag(Base):
    """
    A ``#hashtag`` extracted from a post's content on write.

    ``tag`` is stored normalised (lowercased, no leading ``#``). One row per
    (post, tag): the pair is the primary key, so re-syncing a post's tags is
    idempotent. The index on ``tag`` is what a future trending / hashtag-feed
    query groups by.
    """

    __tablename__ = "post_hashtags"
    __table_args__ = (
        PrimaryKeyConstraint("post_id", "tag", name="pk_post_hashtags"),
        Index("ix_post_hashtags_tag", "tag"),
    )

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    tag: Mapped[str] = mapped_column(String(140), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
