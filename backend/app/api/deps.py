from typing import Annotated

from app.core.config import settings
from app.core.session_store import SessionBackendUnavailable, resolve_session
from fastapi import Cookie, Header, HTTPException, status


def get_current_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id")] = None,
    session_cookie: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
) -> int:
    """
    Resolve the caller's user id from the server-side session.

    ``X-User-Id`` is a plain request header, so trusting it means any client can
    impersonate any user by sending one. It is therefore only honoured when
    ``dev_allow_header_auth`` is explicitly enabled, which config.py refuses to
    combine with a production (HTTPS) cookie setup.
    """
    try:
        user_id = resolve_session(session_cookie)
    except SessionBackendUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session storage is unavailable.",
        ) from exc

    if user_id is not None:
        return user_id

    if settings.dev_allow_header_auth and x_user_id is not None and x_user_id > 0:
        return x_user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid session.",
    )
