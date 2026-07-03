from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import follow_repository, user_repository
from app.schemas.follow import FollowActionOut
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/follows", tags=["follows"])


@router.post("/{followee_id}", response_model=FollowActionOut)
def follow_user(
    followee_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> FollowActionOut:
    if user_repository.get_user(db, followee_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="followee not found",
        )

    if user_repository.get_user(db, current_user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="current user not found",
        )

    try:
        follow_repository.follow_user(
            db,
            follower_id=current_user_id,
            followee_id=followee_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return FollowActionOut(
        follower_id=current_user_id,
        followee_id=followee_id,
        is_following=True,
    )


@router.delete("/{followee_id}", response_model=FollowActionOut)
def unfollow_user(
    followee_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> FollowActionOut:
    if user_repository.get_user(db, current_user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="current user not found",
        )

    follow_repository.unfollow_user(
        db,
        follower_id=current_user_id,
        followee_id=followee_id,
    )

    return FollowActionOut(
        follower_id=current_user_id,
        followee_id=followee_id,
        is_following=False,
    )
