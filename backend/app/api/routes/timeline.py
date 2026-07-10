from typing import Literal

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.schemas.tweet import TimelinePage
from app.services.timeline_service import TimelineService
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get(
    "/home",
    response_model=TimelinePage,
    dependencies=[Depends(rate_limiter("timeline"))],
)
def get_home_timeline(
    limit: int = Query(default=settings.timeline_page_size, ge=1, le=50),
    cursor: str | None = None,
    strategy: Literal["read", "write"] = settings.default_timeline_strategy,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> TimelinePage:
    """
    Return the current user's home timeline.

    Interview-focused points:
    - Supports both fan-out on read and fan-out on write.
    - Uses cursor pagination instead of offset pagination.
    - Delegates business logic to the service layer so the route stays thin.
    """
    service = TimelineService(db)

    try:
        return service.get_home_timeline(
            user_id=current_user_id,
            limit=limit,
            cursor=cursor,
            strategy=strategy,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/for-you",
    response_model=TimelinePage,
    dependencies=[Depends(rate_limiter("timeline"))],
)
def get_for_you_timeline(
    limit: int = Query(default=settings.timeline_page_size, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> TimelinePage:
    service = TimelineService(db)

    try:
        return service.get_for_you_timeline(
            limit=limit,
            cursor=cursor,
            user_id=current_user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
