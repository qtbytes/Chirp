import logging
from datetime import datetime, timezone
from typing import Annotated

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.core.security import hash_password, verify_password
from app.core.session_store import (
    SessionBackendUnavailable,
    create_session,
    destroy_session,
    list_user_sessions,
    revoke_session_by_handle,
    revoke_user_sessions,
    session_handle,
    session_id_from_cookie,
)
from app.core.tokens import (
    TokenBackendUnavailable,
    TokenPurpose,
    issue_token,
    redeem_token,
    revoke_tokens,
)
from app.db.database import get_db
from app.models.user import User
from app.repositories import user_repository
from app.schemas.user import (
    EmailChange,
    EmailVerification,
    ForgotPassword,
    PasswordChange,
    PasswordReset,
    SessionOut,
    UserCreate,
    UserLogin,
    UserSummary,
)
from app.services import mailer
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _unavailable(exc: Exception, what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{what} is unavailable.",
    )


def _send_verification(user: User) -> None:
    """Mint a confirmation link for the address the user has claimed."""
    if user.pending_email is None:
        return

    # One live confirmation link per account: re-claiming an address must not
    # leave the previous link able to confirm the previous address.
    revoke_tokens(TokenPurpose.EMAIL_VERIFICATION, user.id)
    token = issue_token(
        TokenPurpose.EMAIL_VERIFICATION,
        user.id,
        settings.email_verification_token_ttl_seconds,
    )
    mailer.send_email_verification(user.pending_email, user.username, token)


@router.post(
    "/register",
    response_model=UserSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("register", identity="ip"))],
)
def register(
    payload: UserCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UserSummary:
    """
    Create an account and mail a confirmation link.

    The address lands in ``pending_email``; nothing but a redeemed token
    promotes it. Until then the account works normally and simply cannot reset
    its password.
    """
    try:
        user = user_repository.create_user(
            db,
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    # The account exists; a mail server that is down must not undo that. The
    # user can ask for another link from their profile. Loud in the log, quiet
    # in the response.
    try:
        _send_verification(user)
    except (mailer.MailerUnavailable, TokenBackendUnavailable):
        logger.exception("could not send a verification email to user %s", user.id)

    _set_session_cookie(response, user.id, request)
    return UserSummary.model_validate(user)


@router.post(
    "/login",
    response_model=UserSummary,
    dependencies=[Depends(rate_limiter("login", identity="ip"))],
)
def login(
    payload: UserLogin,
    request: Request,
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

    _set_session_cookie(response, user.id, request)
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
    request: Request,
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
        # An attacker who requested a reset link before being locked out must not
        # be able to redeem it afterwards.
        revoke_tokens(TokenPurpose.PASSWORD_RESET, user.id)
    except SessionBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session storage is unavailable.",
        ) from exc
    except TokenBackendUnavailable as exc:
        raise _unavailable(exc, "Token storage") from exc

    user_repository.update_user_password(db, user.id, hash_password(payload.new_password))

    # revoke_user_sessions killed this request's session too. Mint a new one so
    # the device that changed the password stays signed in.
    _set_session_cookie(response, user.id, request)


@router.post(
    "/change-email",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limiter("change_email"))],
)
def change_email(
    payload: EmailChange,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    Claim a new address. The confirmed one does not move until a link is clicked.

    The current password is required, and that requirement is the whole reason
    this is not a field on ``PATCH /users/me``. Reset mail goes to the confirmed
    address; if a stolen cookie could repoint it, the thief would simply set
    their own address, request a reset, and own the account -- walking straight
    around change-password's current-password check.

    Because the confirmed address only moves on confirmation, even a thief who
    *does* know the password cannot silently divert reset mail: the old address
    keeps working until the new one is proven.
    """
    user = user_repository.get_user(db, current_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session user not found",
        )

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="current password is incorrect",
        )

    if user.email == payload.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="that is already your confirmed address",
        )

    if user_repository.get_user_by_email(db, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered",
        )

    user = user_repository.set_pending_email(db, user.id, payload.email)

    try:
        _send_verification(user)
    except mailer.MailerUnavailable as exc:
        raise _unavailable(exc, "Email delivery") from exc
    except TokenBackendUnavailable as exc:
        raise _unavailable(exc, "Token storage") from exc

    return {"pending_email": payload.email}


@router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limiter("resend_verification"))],
)
def resend_verification(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Mail a fresh confirmation link, invalidating the previous one."""
    user = user_repository.get_user(db, current_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session user not found",
        )

    if user.pending_email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no email address is awaiting confirmation",
        )

    try:
        _send_verification(user)
    except mailer.MailerUnavailable as exc:
        raise _unavailable(exc, "Email delivery") from exc
    except TokenBackendUnavailable as exc:
        raise _unavailable(exc, "Token storage") from exc

    return {"pending_email": user.pending_email}


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limiter("verify_email", identity="ip"))],
)
def verify_email(
    payload: EmailVerification,
    db: Session = Depends(get_db),
) -> None:
    """
    Redeem a confirmation link. Unauthenticated: the link arrives by mail, and
    the recipient may well be reading it in a browser that has never logged in.
    """
    try:
        user_id = redeem_token(TokenPurpose.EMAIL_VERIFICATION, payload.token)
    except TokenBackendUnavailable as exc:
        raise _unavailable(exc, "Token storage") from exc

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="this confirmation link is invalid or has expired",
        )

    try:
        user_repository.confirm_pending_email(db, user_id)
    except ValueError as exc:
        # 409 only for a genuine conflict -- somebody else confirmed the address
        # first. "Nothing to confirm" is a spent link, which is a 400 like any
        # other, and must read the same to the caller.
        conflict = "already registered" in str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
            ),
            detail=str(exc) if conflict else "this confirmation link is invalid or has expired",
        ) from exc


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limiter("forgot_password", identity="ip"))],
)
def forgot_password(
    payload: ForgotPassword,
    db: Session = Depends(get_db),
) -> dict:
    """
    Mail a reset link, if the address belongs to a confirmed account.

    Always 202, whatever happens. Answering 404 for an unknown address would
    turn this endpoint into an oracle for "does this person have an account",
    which for a social network is a real disclosure.

    That is also why a failure to send is logged rather than raised: the only
    requests that reach the mailer are the ones where an account exists, so a
    503 here would answer the very question the 202 exists to hide. An operator
    watching the log sees the failure; an attacker watching the response sees
    nothing.
    """
    accepted = {"status": "accepted"}

    user = user_repository.get_user_by_email(db, payload.email)
    if user is None:
        return accepted

    try:
        # One live reset link at a time. A second request invalidates the first,
        # so a link phished out of an inbox goes stale as soon as the real owner
        # asks for their own.
        revoke_tokens(TokenPurpose.PASSWORD_RESET, user.id)
        token = issue_token(
            TokenPurpose.PASSWORD_RESET,
            user.id,
            settings.password_reset_token_ttl_seconds,
        )
        mailer.send_password_reset(user.email, user.username, token)
    except (mailer.MailerUnavailable, TokenBackendUnavailable):
        logger.exception("could not send a password reset email to user %s", user.id)

    return accepted


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limiter("reset_password", identity="ip"))],
)
def reset_password(
    payload: PasswordReset,
    db: Session = Depends(get_db),
) -> None:
    """
    Redeem a reset link and set a new password.

    Deliberately does not sign the caller in. Whoever holds the link may be
    whoever read the mailbox; make them prove they know the password they just
    set. Every existing session dies for the same reason ``change-password``
    kills them -- the old password is presumed lost.
    """
    try:
        user_id = redeem_token(TokenPurpose.PASSWORD_RESET, payload.token)
    except TokenBackendUnavailable as exc:
        raise _unavailable(exc, "Token storage") from exc

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="this reset link is invalid or has expired",
        )

    user = user_repository.get_user(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="this reset link is invalid or has expired",
        )

    # Same ordering rule as change-password: revoke first, write second. If the
    # session store is unreachable we have changed nothing.
    try:
        revoke_user_sessions(user.id)
        revoke_tokens(TokenPurpose.PASSWORD_RESET, user.id)
    except SessionBackendUnavailable as exc:
        raise _unavailable(exc, "Session storage") from exc
    except TokenBackendUnavailable as exc:
        raise _unavailable(exc, "Token storage") from exc

    user_repository.update_user_password(db, user.id, hash_password(payload.new_password))


@router.get(
    "/sessions",
    response_model=list[SessionOut],
    dependencies=[Depends(rate_limiter("list_sessions"))],
)
def list_sessions(
    session_cookie: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
    current_user_id: int = Depends(get_current_user_id),
) -> list[SessionOut]:
    """
    List the caller's active sessions, most recently seen first.

    Each entry is keyed by an opaque handle (``sha256`` of the session id) --
    enough to revoke it, useless for anything else. The session making the
    request is flagged so the UI can label it and withhold a revoke button that
    would only log the caller out.
    """
    current_sid = session_id_from_cookie(session_cookie)
    current_handle = session_handle(current_sid) if current_sid is not None else None

    try:
        sessions = list_user_sessions(current_user_id)
    except SessionBackendUnavailable as exc:
        raise _unavailable(exc, "Session storage") from exc

    return [
        SessionOut(
            id=info.id,
            ip=info.ip,
            user_agent=info.user_agent,
            created_at=datetime.fromtimestamp(info.created_at, tz=timezone.utc),
            last_seen=datetime.fromtimestamp(info.last_seen, tz=timezone.utc),
            current=info.id == current_handle,
        )
        for info in sessions
    ]


@router.post(
    "/logout-others",
    dependencies=[Depends(rate_limiter("revoke_session"))],
)
def logout_others(
    session_cookie: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
    current_user_id: int = Depends(get_current_user_id),
) -> dict:
    """
    End every session except the one making the request.

    Change-password's blast radius without the password change: a user who
    suspects one of their devices signs the rest out and keeps working here. The
    current session is spared by id, so this device is never bounced to login.
    """
    keep_sid = session_id_from_cookie(session_cookie)
    try:
        revoked = revoke_user_sessions(current_user_id, keep_sid=keep_sid)
    except SessionBackendUnavailable as exc:
        raise _unavailable(exc, "Session storage") from exc
    return {"revoked": revoked}


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limiter("revoke_session"))],
)
def revoke_session(
    session_id: str,
    session_cookie: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
    current_user_id: int = Depends(get_current_user_id),
) -> None:
    """
    End one other session by its handle.

    Only the caller's own sessions are searched, so a handle from elsewhere
    reaches nothing. Ending the *current* session is refused here -- that is what
    ``/logout`` is for -- so this endpoint never has to also clear the cookie.
    """
    current_sid = session_id_from_cookie(session_cookie)
    if current_sid is not None and session_handle(current_sid) == session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="use logout to end the current session",
        )

    try:
        removed = revoke_session_by_handle(current_user_id, session_id)
    except SessionBackendUnavailable as exc:
        raise _unavailable(exc, "Session storage") from exc

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
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


# A user agent is a client-supplied string; cap it so a hostile client cannot
# stuff the session hash with an unbounded header.
_USER_AGENT_MAX_LENGTH = 400


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    """The peer address and user-agent to stamp on a new session, for display."""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    if user_agent is not None:
        user_agent = user_agent[:_USER_AGENT_MAX_LENGTH]
    return ip, user_agent


def _set_session_cookie(response: Response, user_id: int, request: Request) -> None:
    ip, user_agent = _client_meta(request)
    response.set_cookie(
        settings.session_cookie_name,
        create_session(user_id, ip=ip, user_agent=user_agent),
        # max_age mirrors the server-side TTL so the browser stops sending a
        # cookie the store would reject anyway. The server remains the
        # authority: a client that ignores max_age still gets a 401.
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
