from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Block(Base):
    """
    ``blocker_id`` has blocked ``blocked_id``.

    A block is one-directional as a record but bidirectional in effect: neither
    party sees the other's posts, replies, or notifications, and neither can
    follow or interact with the other. The read paths union both directions into
    a single "hidden from this viewer" set (see block_repository.hidden_user_ids).
    """

    __tablename__ = "blocks"
    __table_args__ = (
        PrimaryKeyConstraint("blocker_id", "blocked_id", name="pk_blocks"),
        # The leading PK column indexes blocker lookups ("who have I blocked");
        # this covers the reverse ("who has blocked me").
        Index("ix_blocks_blocked", "blocked_id"),
    )

    blocker_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
