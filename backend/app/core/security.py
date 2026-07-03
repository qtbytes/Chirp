import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from app.core.config import settings

HASH_ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        HASH_ITERATIONS,
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
    except (TypeError, ValueError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class SessionCookie:
    user_id: int


def create_session_cookie(user_id: int) -> str:
    encoded_payload = base64.urlsafe_b64encode(str(user_id).encode("ascii")).decode(
        "ascii"
    )
    return f"{encoded_payload}.{_sign(encoded_payload)}"


def parse_session_cookie(raw_cookie: str | None) -> SessionCookie | None:
    if not raw_cookie:
        return None

    try:
        encoded_payload, signature = raw_cookie.split(".", 1)
    except ValueError:
        return None

    if not hmac.compare_digest(signature, _sign(encoded_payload)):
        return None

    try:
        user_id = int(
            base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("ascii")
        )
    except (TypeError, ValueError):
        return None

    if user_id <= 0:
        return None

    return SessionCookie(user_id=user_id)


def _sign(encoded_payload: str) -> str:
    digest = hmac.new(
        settings.session_secret_key.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")
