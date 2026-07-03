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
    Interview-friendly auth shortcut.

    Instead of wiring JWT/session auth first, use the X-User-Id header so you
    can focus on timeline, feed, pagination, cache, and high-concurrency design.

    Production note:
    Replace this with real authentication middleware / dependency later.
    """
    session = parse_session_cookie(session_cookie)
    if session is not None:
        return session.user_id

    if x_user_id is None or x_user_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid session.",
        )
    return x_user_id
