from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import block_repository, user_repository
from app.schemas.block import BlockActionOut, BlockedUserOut, BlockListPage
from app.schemas.user import UserSummary
from app.services.timeline_service import (
    decode_cursor,
    encode_cursor,
    invalidate_timeline_cache_for_users,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/blocks", tags=["blocks"])


@router.get("", response_model=BlockListPage)
def list_blocks(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> BlockListPage:
    """The accounts the caller has blocked, most recent first."""
    cursor_created_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_created_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = block_repository.list_blocked(
        db,
        blocker_id=current_user_id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]
    # Built from a validated ``UserSummary`` rather than field by field: spelling
    # the fields out here silently dropped ``is_deleted`` / ``is_suspended`` to
    # their schema defaults, so a suspended account read as an active one. This
    # form cannot miss a field the summary gains later.
    items = [
        BlockedUserOut(
            **UserSummary.model_validate(row["user"]).model_dump(),
            blocked_at=row["blocked_at"],
        )
        for row in page_rows
    ]

    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["blocked_at"], last["user"].id)

    return BlockListPage(items=items, next_cursor=next_cursor)


@router.post("/{user_id}", response_model=BlockActionOut)
def block_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> BlockActionOut:
    """
    Block a user. Idempotent, and severs any follow between the two.

    Both timelines are invalidated so the block takes effect immediately rather
    than after the cache TTL: the blocker must stop seeing the blocked user's
    posts, and the blocked user must stop seeing the blocker's.
    """
    # A tombstone is not a blockable account: it can never post, follow or
    # message again, so a block against it would only be a dead row that the
    # block list then renders as a real person. ``get_user`` is a plain lookup
    # and does return tombstones, hence the explicit check -- the same one
    # ``_load_counterpart`` makes before opening a DM.
    target = user_repository.get_user(db, user_id)
    if target is None or target.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    try:
        block_repository.block_user(
            db, blocker_id=current_user_id, blocked_id=user_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    invalidate_timeline_cache_for_users([current_user_id, user_id])
    return BlockActionOut(blocked_id=user_id, is_blocked=True)


@router.delete("/{user_id}", response_model=BlockActionOut)
def unblock_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> BlockActionOut:
    """Unblock a user. Follows are not restored; re-follow to reconnect."""
    block_repository.unblock_user(
        db, blocker_id=current_user_id, blocked_id=user_id
    )
    invalidate_timeline_cache_for_users([current_user_id, user_id])
    return BlockActionOut(blocked_id=user_id, is_blocked=False)
