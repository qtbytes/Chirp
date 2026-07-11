from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mute(Base):
    """
    ``muter_id`` has muted ``muted_id``.

    Unlike a block, a mute is one-directional in effect as well as in record: it
    only removes the muted user's content from the muter's own read paths. It
    does not sever follows, is invisible to the muted user, and places no guard
    on interaction -- either party may still follow, like, reply to, or quote the
    other. Only the muter's side is queried ("who have I muted"), so the leading
    primary-key column covers every lookup and no reverse index is needed.
    """

    __tablename__ = "mutes"
    __table_args__ = (
        PrimaryKeyConstraint("muter_id", "muted_id", name="pk_mutes"),
    )

    muter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    muted_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
