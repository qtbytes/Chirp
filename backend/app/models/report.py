from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Report(Base):
    """
    A moderation report: ``reporter_id`` flagged a target with a ``reason``.

    The target is either a post (``post_id``) or an account
    (``reported_user_id``) -- exactly one, enforced by a check constraint. Post
    reports flag content; user reports flag conduct that no single post captures
    (a profile, a DM pattern). Unlike a block or mute, a report changes nothing
    about what the reporter sees -- it is a signal for a moderator, not a
    personal filter. One report per (reporter, target) pair, enforced by unique
    constraints (NULLs are distinct, so each constraint only bites for its own
    target kind); re-reporting the same target just updates the reason rather
    than piling up rows. ``post_id`` points at the unified ``posts`` table, so
    tweets and comments are both reportable.
    """

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "(post_id IS NULL) != (reported_user_id IS NULL)",
            name="ck_report_exactly_one_target",
        ),
        UniqueConstraint("reporter_id", "post_id", name="uq_report_reporter_post"),
        UniqueConstraint(
            "reporter_id", "reported_user_id", name="uq_report_reporter_user"
        ),
        # "Everything reported about post X", the moderator's read.
        Index("ix_reports_post", "post_id"),
        # The queue's read: open reports grouped by target.
        Index("ix_reports_status_post", "status", "post_id"),
        Index("ix_reports_status_user", "status", "reported_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"), nullable=True)
    reported_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[str | None] = mapped_column(String(280), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    # Moderation lifecycle. A report is ``open`` until a moderator judges the
    # *target*, at which point every open report about it closes together:
    # ``dismissed`` (nothing wrong) or ``actioned`` (the post was taken down /
    # the account was suspended).
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open", nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
