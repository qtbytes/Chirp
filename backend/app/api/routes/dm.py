from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.models.user import User
from app.repositories import block_repository, dm_repository, user_repository
from app.schemas.dm import (
    ChatOut,
    ConversationOut,
    ConversationPage,
    DmMessageCreate,
    DmMessageOut,
    DmUnreadCountOut,
)
from app.schemas.user import UserSummary
from app.services.timeline_service import decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/dm", tags=["dm"])


def _load_counterpart(db: Session, username: str, current_user_id: int) -> User:
    """
    Resolve the other participant, refusing self-chat and hiding blocked or
    deleted accounts behind the same 404 the rest of the API uses (a block is
    never disclosed by a distinct error).
    """
    user = user_repository.get_user_by_username(db, username)
    if (
        user is None
        or user.is_deleted
        or block_repository.blocks_between(db, current_user_id, user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )
    if user.id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="you cannot message yourself",
        )
    return user


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ConversationPage:
    cursor_last_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_last_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = dm_repository.list_conversations(
        db,
        user_id=current_user_id,
        limit=limit,
        cursor_last_message_at=cursor_last_at,
        cursor_id=cursor_id,
        exclude_user_ids=block_repository.hidden_user_ids(db, current_user_id),
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]

    items = [
        ConversationOut(
            id=row["conversation"].id,
            other_user=UserSummary.model_validate(row["other_user"]),
            last_message=(
                DmMessageOut.model_validate(row["last_message"])
                if row["last_message"] is not None
                else None
            ),
            unread_count=row["unread_count"],
        )
        for row in page_rows
    ]

    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]["conversation"]
        next_cursor = encode_cursor(last.last_message_at, last.id)

    return ConversationPage(items=items, next_cursor=next_cursor)


@router.get("/unread-count", response_model=DmUnreadCountOut)
def unread_count(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> DmUnreadCountOut:
    return DmUnreadCountOut(
        count=dm_repository.count_unread_total(
            db,
            user_id=current_user_id,
            exclude_user_ids=block_repository.hidden_user_ids(db, current_user_id),
        )
    )


@router.get("/with/{username}", response_model=ChatOut)
def get_chat(
    username: str,
    limit: int = Query(default=30, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ChatOut:
    """
    The chat with ``username``: a page of messages (newest first) and whether
    the caller may send right now. Works before any message exists, so the
    "new chat" screen and an established thread are the same endpoint.
    """
    other = _load_counterpart(db, username, current_user_id)
    conversation = dm_repository.get_conversation(db, current_user_id, other.id)

    messages: list = []
    next_cursor = None
    if conversation is not None:
        rows = dm_repository.list_messages(
            db, conversation, limit=limit, before_id=before_id
        )
        has_next = len(rows) > limit
        messages = rows[:limit]
        if has_next and messages:
            next_cursor = str(messages[-1].id)

    reason = dm_repository.check_can_send(db, current_user_id, other, conversation)
    return ChatOut(
        other_user=UserSummary.model_validate(other),
        messages=[DmMessageOut.model_validate(message) for message in messages],
        next_cursor=next_cursor,
        can_send=reason is None,
        # 'blocked' cannot reach here (a block already 404s above).
        cannot_send_reason=reason,
    )


@router.post(
    "/with/{username}/messages",
    response_model=DmMessageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("dm"))],
)
def send_message(
    username: str,
    payload: DmMessageCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> DmMessageOut:
    other = _load_counterpart(db, username, current_user_id)
    try:
        message = dm_repository.send_message(
            db,
            sender_id=current_user_id,
            recipient=other,
            content=payload.content,
        )
    except PermissionError as exc:
        reason = str(exc)
        if reason == "blocked":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            ) from exc
        detail = (
            "they don't accept new messages"
            if reason == "policy"
            else "wait for a reply before sending more messages"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=detail
        ) from exc
    return DmMessageOut.model_validate(message)


@router.post("/with/{username}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_chat_read(
    username: str,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> None:
    other = _load_counterpart(db, username, current_user_id)
    conversation = dm_repository.get_conversation(db, current_user_id, other.id)
    if conversation is not None:
        dm_repository.mark_read(db, conversation, current_user_id)
