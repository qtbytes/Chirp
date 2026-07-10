"""
Outbound transactional mail: confirm an address, reset a password.

Two backends, chosen by configuration rather than by a flag:

- ``smtp_host`` set -> deliver over SMTP.
- otherwise, in a development configuration -> print the message to the log, so
  a contributor can follow a reset link without standing up a mail server.

A production configuration (``session_cookie_secure``) with no ``smtp_host`` is
an error, not a third mode. The console sender would write reset tokens to the
server log and tell the user "we emailed you a link", a lie nobody notices until
someone is locked out. So ``send_email`` raises instead.

Callers decide what a raise means. ``change-email`` and ``resend-verification``
turn it into a 503. ``register`` logs it and still creates the account. And
``forgot-password`` logs it and still answers 202 -- it only ever reaches the
mailer when the account exists, so a 503 there would answer the very question
its uniform 202 exists to hide.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from app.core.config import settings

logger = logging.getLogger(__name__)


class MailerUnavailable(RuntimeError):
    """Mail cannot be delivered and printing it to the log would be unsafe."""


def _console_send(message: EmailMessage) -> None:
    logger.warning(
        "SMTP is not configured; printing mail instead of sending it.\n"
        "--- to: %s\n--- subject: %s\n%s",
        message["To"],
        message["Subject"],
        message.get_content(),
    )


def _smtp_send(message: EmailMessage) -> None:
    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_email(to: str, subject: str, body: str) -> None:
    """
    Deliver one message, or raise.

    Raising matters: the caller answers 202 to keep an address from being
    probed, and that answer is only honest if a failure to send is loud on the
    server side rather than swallowed here.
    """
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    if settings.smtp_host:
        try:
            _smtp_send(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailerUnavailable(f"failed to send mail to {to}") from exc
        return

    if settings.session_cookie_secure:
        raise MailerUnavailable(
            "SMTP is not configured. Set SMTP_HOST, or accept that password "
            "reset and email confirmation cannot work."
        )

    _console_send(message)


def _link(path: str, token: str) -> str:
    # quote(): a token is url-safe base64 by construction, but the day someone
    # changes how tokens are minted, the link should not silently break.
    return f"{settings.frontend_origin.rstrip('/')}{path}?token={quote(token)}"


def send_email_verification(to: str, username: str, token: str) -> None:
    hours = settings.email_verification_token_ttl_seconds // 3600
    send_email(
        to,
        "Confirm your Chirp email address",
        f"Hi {username},\n\n"
        "Confirm this address so you can reset your password if you ever lose it:\n\n"
        f"{_link('/verify-email', token)}\n\n"
        f"The link works once and expires in {hours} hours.\n"
        "If you did not create a Chirp account, ignore this message.\n",
    )


def send_password_reset(to: str, username: str, token: str) -> None:
    minutes = settings.password_reset_token_ttl_seconds // 60
    send_email(
        to,
        "Reset your Chirp password",
        f"Hi {username},\n\n"
        "Use this link to choose a new password:\n\n"
        f"{_link('/reset-password', token)}\n\n"
        f"The link works once and expires in {minutes} minutes.\n"
        "If you did not ask to reset your password, ignore this message -- your "
        "password has not changed.\n",
    )
