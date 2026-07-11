from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import mute_repository, user_repository
from app.schemas.mute import MuteActionOut, MutedUserOut, MuteListPage
from app.services.timeline_service import (
    decode_cursor,
    encode_cursor,
    invalidate_timeline_cache_for_users,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/mutes", tags=["mutes"])


@router.get("", response_model=MuteListPage)
def list_mutes(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> MuteListPage:
    """The accounts the caller has muted, most recent first."""
    cursor_created_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_created_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = mute_repository.list_muted(
        db,
        muter_id=current_user_id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]
    items = [
        MutedUserOut(
            id=row["user"].id,
            username=row["user"].username,
            display_name=row["user"].display_name,
            created_at=row["user"].created_at,
            avatar_url=row["user"].avatar_url,
            muted_at=row["muted_at"],
        )
        for row in page_rows
    ]

    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["muted_at"], last["user"].id)

    return MuteListPage(items=items, next_cursor=next_cursor)


@router.post("/{user_id}", response_model=MuteActionOut)
def mute_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> MuteActionOut:
    """
    Mute a user. Idempotent, and leaves every follow between the two intact.

    Only the caller's own timeline is invalidated -- a mute is one-directional,
    so the muted user's feed is untouched -- letting the mute take effect
    immediately rather than after the cache TTL.
    """
    if user_repository.get_user(db, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    try:
        mute_repository.mute_user(
            db, muter_id=current_user_id, muted_id=user_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    invalidate_timeline_cache_for_users([current_user_id])
    return MuteActionOut(muted_id=user_id, is_muted=True)


@router.delete("/{user_id}", response_model=MuteActionOut)
def unmute_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> MuteActionOut:
    """Unmute a user; their content reappears in the caller's read paths."""
    mute_repository.unmute_user(
        db, muter_id=current_user_id, muted_id=user_id
    )
    invalidate_timeline_cache_for_users([current_user_id])
    return MuteActionOut(muted_id=user_id, is_muted=False)
