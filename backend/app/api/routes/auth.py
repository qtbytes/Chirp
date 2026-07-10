from typing import Annotated

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.core.security import hash_password, verify_password
from app.core.session_store import (
    SessionBackendUnavailable,
    create_session,
    destroy_session,
    revoke_user_sessions,
)
from app.db.database import get_db
from app.repositories import user_repository
from app.schemas.user import PasswordChange, UserCreate, UserLogin, UserSummary
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


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limiter("change_password"))],
)
def change_password(
    payload: PasswordChange,
    response: Response,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> None:
    """
    Change the caller's password, logging every other device out.

    Changing a password is what you do when you think it leaked, so every
    session minted with the old one has to die -- that is the whole point of
    keeping sessions server-side. The caller's own device is then handed a fresh
    session rather than being bounced to the login screen.

    There is no reset counterpart: with no email on ``User`` there is nothing to
    send a token to, so a forgotten password stays an operator job (see
    ``deploy/set_password.py``).
    """
    user = user_repository.get_user(db, current_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session user not found",
        )

    # A stolen cookie must not be enough to take the account over: proving
    # knowledge of the current password is what separates the two.
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="current password is incorrect",
        )

    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new password must differ from the current one",
        )

    # Revoke before writing the new hash, not after. If the revoke fails we have
    # changed nothing and can fail the request; if the write succeeded first and
    # the revoke then failed, sessions opened with the leaked password would
    # survive a change made specifically to kill them.
    try:
        revoke_user_sessions(user.id)
    except SessionBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session storage is unavailable.",
        ) from exc

    user_repository.update_user_password(db, user.id, hash_password(payload.new_password))

    # revoke_user_sessions killed this request's session too. Mint a new one so
    # the device that changed the password stays signed in.
    _set_session_cookie(response, user.id)


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
