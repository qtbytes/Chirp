from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        PrimaryKeyConstraint("follower_id", "followee_id", name="pk_follows"),
        Index("ix_follows_followee_created", "followee_id", "created_at"),
    )

    follower_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    followee_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
