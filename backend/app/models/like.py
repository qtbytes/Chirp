from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Like(Base):
    __tablename__ = "likes"
    __table_args__ = (PrimaryKeyConstraint("user_id", "tweet_id", name="pk_likes"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    tweet_id: Mapped[int] = mapped_column(
        ForeignKey("tweets.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
