from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.models.comment import Comment
from app.repositories import engagement_repository
from app.schemas.comment import CommentCreate, CommentOut, CommentStatsOut
from app.schemas.user import UserSummary
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/comments", tags=["comments"])


@router.get("/stats", response_model=list[CommentStatsOut])
def list_comment_stats(
    ids: str = Query(..., min_length=1),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[CommentStatsOut]:
    try:
        comment_ids = [int(value) for value in ids.split(",") if value.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must be comma-separated integers",
        ) from exc

    if not comment_ids or any(comment_id <= 0 for comment_id in comment_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must contain positive integers",
        )
    if len(comment_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids supports at most 100 comments",
        )

    return [
        CommentStatsOut(**stats)
        for stats in engagement_repository.list_comment_stats(
            db,
            comment_ids=comment_ids,
            current_user_id=current_user_id,
        )
    ]


@router.post("/{comment_id}/likes", status_code=status.HTTP_201_CREATED)
def like_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = engagement_repository.like_comment(
            db,
            user_id=current_user_id,
            comment_id=comment_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "comment_id": comment_id,
        "liked": True,
        "created": created,
    }


@router.delete("/{comment_id}/likes", status_code=status.HTTP_200_OK)
def unlike_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    removed = engagement_repository.unlike_comment(
        db,
        user_id=current_user_id,
        comment_id=comment_id,
    )
    return {
        "comment_id": comment_id,
        "liked": False,
        "removed": removed,
    }


@router.post("/{comment_id}/likes/toggle", status_code=status.HTTP_200_OK)
def toggle_comment_like(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if engagement_repository.has_liked_comment(
        db,
        user_id=current_user_id,
        comment_id=comment_id,
    ):
        engagement_repository.unlike_comment(
            db,
            user_id=current_user_id,
            comment_id=comment_id,
        )
        liked = False
    else:
        try:
            engagement_repository.like_comment(
                db,
                user_id=current_user_id,
                comment_id=comment_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        liked = True

    return {
        "comment_id": comment_id,
        "liked": liked,
        "like_count": engagement_repository.count_comment_likes(db, comment_id),
    }


@router.post("/{comment_id}/retweets", status_code=status.HTTP_201_CREATED)
def retweet_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = engagement_repository.retweet_comment(
            db,
            user_id=current_user_id,
            comment_id=comment_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "comment_id": comment_id,
        "retweeted": True,
        "created": created,
    }


@router.delete("/{comment_id}/retweets", status_code=status.HTTP_200_OK)
def unretweet_comment(
    comment_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    removed = engagement_repository.unretweet_comment(
        db,
        user_id=current_user_id,
        comment_id=comment_id,
    )
    return {
        "comment_id": comment_id,
        "retweeted": False,
        "removed": removed,
    }


@router.post(
    "/{comment_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def reply_to_comment(
    comment_id: int,
    payload: CommentCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> CommentOut:
    parent = db.get(Comment, comment_id)
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="comment not found",
        )

    try:
        comment, author = engagement_repository.create_comment(
            db,
            user_id=current_user_id,
            tweet_id=parent.tweet_id,
            parent_comment_id=comment_id,
            content=payload.content,
            media_url=payload.media_url,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return CommentOut(
        id=comment.id,
        tweet_id=comment.tweet_id,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        media_url=comment.media_url,
        created_at=comment.created_at,
        author=UserSummary.model_validate(author),
        like_count=0,
        comment_count=0,
        retweet_count=0,
        liked_by_me=False,
    )
