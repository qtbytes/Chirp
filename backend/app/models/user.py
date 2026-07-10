from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """
    ``email`` is the address the user has *proven* they control; ``pending_email``
    is one they have only claimed. Registration and email changes write the
    claim, and confirming a mailed token promotes it.

    Only ``email`` is unique, and only ``email`` is matched by password reset. Two
    people may both claim an address; at most one can confirm it, and the loser's
    claim is simply never promoted. Keeping the claim out of the unique index is
    what stops an attacker from squatting an address they cannot receive mail at.

    Both are nullable: accounts predating the email column have neither, and can
    log in but not reset until they add one. 254 is the RFC 5321 maximum.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    email: Mapped[str | None] = mapped_column(
        String(254), unique=True, index=True, nullable=True
    )
    pending_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(160), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    posts = relationship(
        "Post", back_populates="author", cascade="all, delete-orphan"
    )
