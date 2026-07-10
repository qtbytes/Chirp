from typing import Annotated

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.core.security import hash_password, verify_password
from app.core.session_store import create_session, destroy_session
from app.db.database import get_db
from app.repositories import user_repository
from app.schemas.user import UserCreate, UserLogin, UserSummary
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("register", identity="ip"))],
)
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


@router.post(
    "/login",
    response_model=UserSummary,
    dependencies=[Depends(rate_limiter("login", identity="ip"))],
)
def login(
    payload: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
) -> UserSummary:
    """
    Exchange credentials for a session cookie.

    Rate limited by peer address, not by session: an attacker guessing passwords
    may well be holding a valid cookie of their own, and bucketing on it would
    hand them a fresh allowance per guess.

    Bucketing by IP stops one host from grinding through a credential dump. It
    does not stop the same dump replayed from a botnet, and deliberately does not
    throttle per *username*: a username bucket lets anyone lock a known account
    out on demand, which trades an attack we cannot fully block for one that is
    trivial. Password strength and (eventually) MFA are the answer there.
    """
    user = user_repository.get_user_by_username(db, payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )

    _set_session_cookie(response, user.id)
    return UserSummary.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session_cookie: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
) -> None:
    # Delete the server-side record first: clearing the browser's copy alone
    # would leave a captured cookie value working until it expired.
    destroy_session(session_cookie)

    # Browsers only drop the cookie when the clearing attributes match the
    # ones it was set with.
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


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
        create_session(user_id),
        # max_age mirrors the server-side TTL so the browser stops sending a
        # cookie the store would reject anyway. The server remains the
        # authority: a client that ignores max_age still gets a 401.
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
