"""
Direct messages: 1:1 conversations with two send-time gates.

**Anti-spam (the bilibili rule).** There is no "message requests" inbox.
Instead, until the other side has replied, a sender may have at most ONE
message in the conversation. The first reply from the recipient opens the
conversation permanently -- after that both sides send freely. This keeps a
stranger's reach to a single unanswered message without hiding it in a
separate requests queue.

**Policy.** ``users.dm_policy`` says who may start a conversation with the
user: 'everyone', 'following' (only people the user follows), or 'none'. The
policy gates *unestablished* conversations only -- once the recipient has
replied, the chat stays open even if they later tighten the policy (matching
how Twitter's setting affects new requests, not existing threads).
"""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.dm import Conversation, DmMessage
from app.models.user import User
from app.repositories.block_repository import blocks_between
from app.repositories.follow_repository import is_following
from app.services.events import queue_user_event

DM_POLICIES = frozenset(("everyone", "following", "none"))


def _pair(user_a: int, user_b: int) -> tuple[int, int]:
    return (user_a, user_b) if user_a < user_b else (user_b, user_a)


def get_conversation(db: Session, user_a: int, user_b: int) -> Conversation | None:
    low, high = _pair(user_a, user_b)
    return db.scalar(
        select(Conversation).where(
            Conversation.user_low_id == low,
            Conversation.user_high_id == high,
        )
    )


def check_can_send(
    db: Session,
    sender_id: int,
    recipient: User,
    conversation: Conversation | None,
) -> str | None:
    """
    Whether ``sender_id`` may message ``recipient`` right now; ``None`` for
    yes, otherwise a reason code: 'blocked', 'policy', or 'await_reply'.
    """
    if recipient.is_deleted or blocks_between(db, sender_id, recipient.id):
        return "blocked"

    if conversation is not None:
        recipient_has_replied = (
            db.scalar(
                select(DmMessage.id)
                .where(
                    DmMessage.conversation_id == conversation.id,
                    DmMessage.sender_id == recipient.id,
                )
                .limit(1)
            )
            is not None
        )
        if recipient_has_replied:
            # Established conversation: no policy, no message cap.
            return None

        sent_count = int(
            db.scalar(
                select(func.count())
                .select_from(DmMessage)
                .where(
                    DmMessage.conversation_id == conversation.id,
                    DmMessage.sender_id == sender_id,
                )
            )
            or 0
        )
        if sent_count >= 1:
            return "await_reply"

    if recipient.dm_policy == "none":
        return "policy"
    if recipient.dm_policy == "following" and not is_following(
        db, follower_id=recipient.id, followee_id=sender_id
    ):
        return "policy"
    return None


def send_message(
    db: Session,
    sender_id: int,
    recipient: User,
    content: str,
) -> DmMessage:
    """
    Deliver a message, creating the conversation on first contact. Raises
    ``PermissionError(reason)`` when a send-time gate refuses it.
    """
    conversation = get_conversation(db, sender_id, recipient.id)
    reason = check_can_send(db, sender_id, recipient, conversation)
    if reason is not None:
        raise PermissionError(reason)

    if conversation is None:
        low, high = _pair(sender_id, recipient.id)
        conversation = Conversation(user_low_id=low, user_high_id=high)
        db.add(conversation)
        db.flush()

    message = DmMessage(
        conversation_id=conversation.id,
        sender_id=sender_id,
        content=content,
    )
    db.add(message)
    db.flush()
    conversation.last_message_at = message.created_at
    # Your own message never counts as unread for you.
    conversation.set_last_read_id(sender_id, message.id)

    # Nudge the recipient's open tabs (SSE) to re-read their unread count.
    queue_user_event(db, recipient.id)
    db.commit()
    db.refresh(message)
    return message


def _unread_count(db: Session, conversation: Conversation, viewer_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(DmMessage)
        .where(
            DmMessage.conversation_id == conversation.id,
            DmMessage.sender_id != viewer_id,
        )
    )
    last_read = conversation.last_read_id(viewer_id)
    if last_read is not None:
        stmt = stmt.where(DmMessage.id > last_read)
    return int(db.scalar(stmt) or 0)


def list_conversations(
    db: Session,
    user_id: int,
    limit: int,
    cursor_last_message_at: datetime | None = None,
    cursor_id: int | None = None,
    exclude_user_ids: set[int] | None = None,
) -> list[dict]:
    """
    The user's inbox, newest activity first: each conversation with the other
    participant, its latest message, and the viewer's unread count. Empty
    conversations (no message yet) never exist -- rows are only created on the
    first send. Conversations with blocked/blocking or deleted users are
    hidden, not deleted: unblocking brings the history back.
    """
    stmt = (
        select(Conversation)
        .where(
            or_(
                Conversation.user_low_id == user_id,
                Conversation.user_high_id == user_id,
            ),
            Conversation.last_message_at.is_not(None),
        )
        .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        .limit(limit + 1)
    )
    if cursor_last_message_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Conversation.last_message_at < cursor_last_message_at,
                (Conversation.last_message_at == cursor_last_message_at)
                & (Conversation.id < cursor_id),
            )
        )

    rows: list[dict] = []
    for conversation in db.scalars(stmt).all():
        other_id = conversation.other_user_id(user_id)
        if exclude_user_ids and other_id in exclude_user_ids:
            continue
        other = db.get(User, other_id)
        if other is None or other.is_deleted:
            continue
        last_message = db.scalar(
            select(DmMessage)
            .where(DmMessage.conversation_id == conversation.id)
            .order_by(DmMessage.id.desc())
            .limit(1)
        )
        rows.append(
            {
                "conversation": conversation,
                "other_user": other,
                "last_message": last_message,
                "unread_count": _unread_count(db, conversation, user_id),
            }
        )
    return rows


def list_messages(
    db: Session,
    conversation: Conversation,
    limit: int,
    before_id: int | None = None,
) -> list[DmMessage]:
    """A page of messages newest-first; ``before_id`` pages further back."""
    stmt = (
        select(DmMessage)
        .where(DmMessage.conversation_id == conversation.id)
        .order_by(DmMessage.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        stmt = stmt.where(DmMessage.id < before_id)
    return list(db.scalars(stmt).all())


def mark_read(db: Session, conversation: Conversation, viewer_id: int) -> None:
    """Move the viewer's read marker to the newest message."""
    newest_id = db.scalar(
        select(func.max(DmMessage.id)).where(
            DmMessage.conversation_id == conversation.id
        )
    )
    if newest_id is not None and (conversation.last_read_id(viewer_id) or 0) < newest_id:
        conversation.set_last_read_id(viewer_id, newest_id)
        db.commit()


def count_unread_total(
    db: Session, user_id: int, exclude_user_ids: set[int] | None = None
) -> int:
    """Total unread messages across conversations, for the rail badge."""
    conversations = db.scalars(
        select(Conversation).where(
            or_(
                Conversation.user_low_id == user_id,
                Conversation.user_high_id == user_id,
            ),
            Conversation.last_message_at.is_not(None),
        )
    ).all()
    total = 0
    for conversation in conversations:
        other_id = conversation.other_user_id(user_id)
        if exclude_user_ids and other_id in exclude_user_ids:
            continue
        total += _unread_count(db, conversation, user_id)
    return total
