from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeedItem(Base):
    """
    Precomputed home timeline row used by fan-out on write.

    Interview notes:
    - owner_id: the user who sees this feed item in their home timeline
    - post_id: the actual post shown in the timeline
    - actor_id: the author who created the post
    - created_at: ordering key for cursor pagination
    """

    __tablename__ = "feed_items"
    __table_args__ = (
        UniqueConstraint("owner_id", "post_id", name="uq_feed_owner_post"),
        Index("ix_feed_owner_created_id", "owner_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id"),
        nullable=False,
    )
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
