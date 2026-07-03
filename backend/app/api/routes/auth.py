from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.security import create_session_cookie, hash_password, verify_password
from app.db.database import get_db
from app.repositories import user_repository
from app.schemas.user import UserCreate, UserLogin, UserSummary
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def register(
    payload: UserCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> UserSummary:
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

    _set_session_cookie(response, user.id)
    return UserSummary.model_validate(user)


@router.post("/login", response_model=UserSummary)
def login(
    payload: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
) -> UserSummary:
    user = user_repository.get_user_by_username(db, payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )

    _set_session_cookie(response, user.id)
    return UserSummary.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserSummary)
def me(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserSummary:
    user = user_repository.get_user(db, current_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session user not found",
        )
    return UserSummary.model_validate(user)


def _set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        create_session_cookie(user_id),
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
