from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import notification_repository
from app.schemas.notification import NotificationOut, UnreadCountOut
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    limit: int = Query(default=30, ge=1, le=50),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[NotificationOut]:
    rows = notification_repository.list_notifications(
        db, user_id=current_user_id, limit=limit
    )
    return [NotificationOut.model_validate(row) for row in rows]


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UnreadCountOut:
    return UnreadCountOut(
        count=notification_repository.count_unread(db, user_id=current_user_id)
    )


@router.post("/mark-read", status_code=status.HTTP_200_OK)
def mark_read(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    updated = notification_repository.mark_all_read(db, user_id=current_user_id)
    return {"updated": updated}
