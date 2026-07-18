from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    """
    A 1:1 direct-message thread. No group chats: exactly two participants,
    stored normalized as ``(user_low_id, user_high_id)`` with low < high, so
    each pair has exactly one row regardless of who wrote first.

    ``last_message_at`` orders the inbox (denormalized from the newest
    message). ``low/high_last_read_message_id`` is each participant's read
    marker: their unread count is the other side's messages with a higher id.
    Two columns instead of a join table -- with exactly two participants there
    is nothing to normalize.
    """

    __tablename__ = "dm_conversations"
    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_dm_conversations_pair"),
        Index("ix_dm_conversations_low_last", "user_low_id", "last_message_at"),
        Index("ix_dm_conversations_high_last", "user_high_id", "last_message_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_low_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    user_high_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    low_last_read_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    high_last_read_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Per-participant mute: the chat stays, but stops counting toward the
    # muter's unread badge. The other side is never told.
    low_muted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    high_muted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # Per-participant "deleted the conversation" watermark: messages with id
    # <= the marker are invisible to that participant, and a conversation with
    # nothing visible left drops out of their inbox. One-directional -- the
    # other side keeps the full history -- and messages are never actually
    # removed, so a later message revives the chat from that point on.
    low_cleared_before_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    high_cleared_before_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def other_user_id(self, user_id: int) -> int:
        return self.user_high_id if user_id == self.user_low_id else self.user_low_id

    def last_read_id(self, user_id: int) -> int | None:
        if user_id == self.user_low_id:
            return self.low_last_read_message_id
        return self.high_last_read_message_id

    def set_last_read_id(self, user_id: int, message_id: int) -> None:
        if user_id == self.user_low_id:
            self.low_last_read_message_id = message_id
        else:
            self.high_last_read_message_id = message_id

    def muted_for(self, user_id: int) -> bool:
        return self.low_muted if user_id == self.user_low_id else self.high_muted

    def set_muted(self, user_id: int, muted: bool) -> None:
        if user_id == self.user_low_id:
            self.low_muted = muted
        else:
            self.high_muted = muted

    def cleared_before_id(self, user_id: int) -> int | None:
        if user_id == self.user_low_id:
            return self.low_cleared_before_id
        return self.high_cleared_before_id

    def set_cleared_before_id(self, user_id: int, message_id: int) -> None:
        if user_id == self.user_low_id:
            self.low_cleared_before_id = message_id
        else:
            self.high_cleared_before_id = message_id


class DmMessage(Base):
    """
    One direct message. ``id`` is monotonic, so ordering and read markers both
    key on it within a conversation.
    """

    __tablename__ = "dm_messages"
    __table_args__ = (Index("ix_dm_messages_conversation", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("dm_conversations.id"), nullable=False
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
