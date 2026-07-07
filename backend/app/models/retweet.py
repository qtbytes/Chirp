from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Retweet(Base):
    __tablename__ = "retweets"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "post_id", name="pk_retweets"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
