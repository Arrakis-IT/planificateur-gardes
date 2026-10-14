# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Outbound email over SMTP: Brevo in production, Mailpit in development."""

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol

import anyio

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Attachment:
    """A file attached to an email."""

    filename: str
    content: bytes
    mime_type: str = "application/pdf"


@dataclass(frozen=True)
class OutgoingEmail:
    """A message ready to send (plain text + optional HTML alternative)."""

    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
    headers: tuple[tuple[str, str], ...] = ()  # extra headers, e.g. List-Unsubscribe


class MailSender(Protocol):
    """Anything able to send an email (real SMTP or a test fake)."""

    async def send(self, message: OutgoingEmail) -> None:
        """Send the message or raise."""
        ...


def tls_context() -> ssl.SSLContext:
    """TLS context for the Brevo relay: certificate AND hostname are verified.

    `ssl.create_default_context()` already sets CERT_REQUIRED and check_hostname=True; they
    are restated here so the intent is explicit (static analysers flag STARTTLS without a
    visibly verifying context). Minimum protocol: TLS 1.2.
    """
    context = ssl.create_default_context()
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class SmtpMailSender:
    """Send via SMTP. Uses the dev relay (Mailpit) when MAIL_DEV_HOST is set."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build(self, message: OutgoingEmail) -> EmailMessage:
        s = self._settings
        msg = EmailMessage()
        msg["From"] = formataddr((s.brevo_sender_name, s.brevo_sender_email))
        msg["To"] = message.to
        msg["Subject"] = message.subject
        for name, value in message.headers:
            msg[name] = value
        msg["Message-ID"] = make_msgid(domain=s.brevo_sender_email.split("@")[-1])
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")
        for att in message.attachments:
            maintype, subtype = att.mime_type.split("/", 1)
            msg.add_attachment(
                att.content, maintype=maintype, subtype=subtype, filename=att.filename
            )
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        s = self._settings
        if s.mail_dev_host:
            with smtplib.SMTP(
                s.mail_dev_host, s.mail_dev_port or 1025, timeout=SMTP_TIMEOUT_SECONDS
            ) as smtp:
                smtp.send_message(msg)
            return
        with smtplib.SMTP(
            s.brevo_smtp_host, s.brevo_smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        ) as smtp:
            # Server certificate and hostname validated (see tls_context).
            smtp.starttls(context=tls_context())
            if s.brevo_smtp_user and s.brevo_smtp_password:
                smtp.login(s.brevo_smtp_user, s.brevo_smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def send(self, message: OutgoingEmail) -> None:
        """Send in a worker thread (smtplib is blocking)."""
        msg = self._build(message)
        await anyio.to_thread.run_sync(self._send_sync, msg)
        logger.info("Email sent (subject=%r)", message.subject)


def get_mail_sender() -> MailSender:
    """FastAPI dependency; overridden in tests."""
    return SmtpMailSender(get_settings())
