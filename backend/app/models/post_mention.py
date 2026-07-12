from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PostMention(Base):
    """
    A resolved ``@mention`` extracted from a post's content on write.

    One row per (post, mentioned user): the pair is the primary key, so
    re-syncing a post's mentions on edit is idempotent. The index on
    ``mentioned_user_id`` supports "posts that mention me" lookups.
    """

    __tablename__ = "post_mentions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "post_id", "mentioned_user_id", name="pk_post_mentions"
        ),
        Index("ix_post_mentions_user", "mentioned_user_id"),
    )

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    mentioned_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
