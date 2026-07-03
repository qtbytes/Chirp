from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import engagement_repository
from app.schemas.comment import CommentCreate, CommentOut
from app.schemas.user import UserSummary
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/tweets", tags=["interactions"])


@router.post("/{tweet_id}/likes", status_code=status.HTTP_201_CREATED)
def like_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    Like a tweet.

    Interview points:
    - Keep the API idempotent. If the user already liked the tweet,
      return created=False instead of raising an error.
    - Validate the target tweet exists in the repository layer.
    """
    try:
        created = engagement_repository.like_tweet(
            db,
            user_id=current_user_id,
            tweet_id=tweet_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "tweet_id": tweet_id,
        "liked": True,
        "created": created,
    }


@router.delete("/{tweet_id}/likes", status_code=status.HTTP_200_OK)
def unlike_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    Unlike a tweet.

    Returning removed=False keeps the endpoint safe to retry and easy to
    discuss in interviews as an idempotent delete operation.
    """
    removed = engagement_repository.unlike_tweet(
        db,
        user_id=current_user_id,
        tweet_id=tweet_id,
    )
    return {
        "tweet_id": tweet_id,
        "liked": False,
        "removed": removed,
    }


@router.post(
    "/{tweet_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    tweet_id: int,
    payload: CommentCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> CommentOut:
    """
    Create a comment on a tweet.

    Interview points:
    - Validate the tweet and user exist.
    - Return comment + author together to avoid extra lookups later.
    """
    try:
        comment, author = engagement_repository.create_comment(
            db,
            user_id=current_user_id,
            tweet_id=tweet_id,
            content=payload.content,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return CommentOut(
        id=comment.id,
        tweet_id=comment.tweet_id,
        content=comment.content,
        created_at=comment.created_at,
        author=UserSummary.model_validate(author),
    )


@router.get(
    "/{tweet_id}/comments",
    response_model=list[CommentOut],
    status_code=status.HTTP_200_OK,
)
def list_comments(
    tweet_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> list[CommentOut]:
    """
    List recent comments for a tweet.

    Interview points:
    - Repository uses a joined query to avoid N+1 when loading authors.
    - In production, this can also use cursor pagination for deep threads.
    """
    try:
        rows = engagement_repository.list_comments_by_tweet(
            db,
            tweet_id=tweet_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return [
        CommentOut(
            id=comment.id,
            tweet_id=comment.tweet_id,
            content=comment.content,
            created_at=comment.created_at,
            author=UserSummary.model_validate(author),
        )
        for comment, author in rows
    ]
