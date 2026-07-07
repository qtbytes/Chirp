from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Notification(Base):
    """
    A single notification delivered to a user (the recipient) about something an
    actor did to their content: like, retweet, comment, reply, or follow.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Recipient (who sees the notification).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    # Actor (who performed the action).
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
        nullable=False,
    )
