from typing import Annotated

from app.core.config import settings
from app.core.security import parse_session_cookie
from fastapi import Cookie, Header, HTTPException, status


def get_current_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id")] = None,
    session_cookie: Annotated[
        str | None,
        Cookie(alias=settings.session_cookie_name),
    ] = None,
) -> int:
    """
    Resolve the caller's user id from the signed session cookie.

    ``X-User-Id`` is a plain request header, so trusting it means any client can
    impersonate any user by sending one. It is therefore only honoured when
    ``dev_allow_header_auth`` is explicitly enabled, which config.py refuses to
    combine with a production (HTTPS) cookie setup.
    """
    session = parse_session_cookie(session_cookie)
    if session is not None:
        return session.user_id

    if settings.dev_allow_header_auth and x_user_id is not None and x_user_id > 0:
        return x_user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid session.",
    )
