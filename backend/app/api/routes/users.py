from app.api.deps import get_current_user_id
from app.core.security import hash_password
from app.db.database import get_db
from app.repositories import user_repository
from app.schemas.user import UserCreate, UserDiscoveryOut, UserSummary
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> UserSummary:
    """
    Create a user.

    Why keep this route simple?
    - The interview focus for this project is timeline/feed design.
    - A lightweight user route lets you create test users quickly.
    """
    try:
        user = user_repository.create_user(
            db,
            username=payload.username,
            password_hash=hash_password(payload.password),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return UserSummary.model_validate(user)


@router.get("", response_model=list[UserDiscoveryOut])
def list_users(
    query: str | None = Query(default=None, min_length=1, max_length=50),
    limit: int = Query(default=10, ge=1, le=50),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[UserDiscoveryOut]:
    rows = user_repository.list_users(
        db,
        current_user_id=current_user_id,
        query=query,
        limit=limit,
    )
    return [
        UserDiscoveryOut(
            id=user.id,
            username=user.username,
            created_at=user.created_at,
            is_following=is_following,
            is_current_user=user.id == current_user_id,
        )
        for user, is_following in rows
    ]
