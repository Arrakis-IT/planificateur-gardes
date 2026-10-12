# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""SMTP sender and Turnstile verifier, without network access."""

import ssl
from typing import Any, ClassVar

import httpx
import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.services import mail as mail_module
from app.services import turnstile as turnstile_module
from app.services.mail import Attachment, OutgoingEmail, SmtpMailSender
from app.services.turnstile import CloudflareTurnstileVerifier


class FakeSMTP:
    """Records what smtplib.SMTP would have done."""

    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port = host, port
        self.calls: list[tuple[str, Any]] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append(("quit", None))

    def starttls(self, context: Any) -> None:
        self.calls.append(("starttls", context))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, msg: Any) -> None:
        self.calls.append(("send", msg))


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(mail_module.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


MESSAGE = OutgoingEmail(
    to="doc@example.lu",
    subject="Sujet accentué",
    text="Bonjour",
    html="<p>Bonjour</p>",
    attachments=(Attachment("planning.pdf", b"%PDF-1.7"),),
)


async def test_dev_relay_used_when_configured(fake_smtp):
    settings = get_settings().model_copy(update={"mail_dev_host": "mailpit", "mail_dev_port": 1025})
    await SmtpMailSender(settings).send(MESSAGE)
    smtp = fake_smtp.instances[0]
    assert (smtp.host, smtp.port) == ("mailpit", 1025)
    assert [c[0] for c in smtp.calls] == ["send", "quit"]  # no TLS, no auth with Mailpit
    msg = smtp.calls[0][1]
    assert msg["To"] == "doc@example.lu"
    assert msg["Subject"] == "Sujet accentué"
    assert "Planificateur des gardes" in msg["From"]
    assert [p.get_filename() for p in msg.iter_attachments()] == ["planning.pdf"]


async def test_brevo_uses_starttls_and_login(fake_smtp):
    settings = get_settings().model_copy(
        update={
            "mail_dev_host": None,
            "brevo_smtp_user": "user@brevo",
            "brevo_smtp_password": SecretStr("smtp-secret"),
        }
    )
    await SmtpMailSender(settings).send(MESSAGE)
    smtp = fake_smtp.instances[0]
    assert (smtp.host, smtp.port) == ("smtp-relay.brevo.com", 587)
    assert [c[0] for c in smtp.calls] == ["starttls", "login", "send", "quit"]
    context = smtp.calls[0][1]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _verifier_with(monkeypatch, handler, **overrides):
    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(turnstile_module.httpx, "AsyncClient", client_factory)
    settings = get_settings().model_copy(
        update={"turnstile_enabled": True, "turnstile_secret_key": SecretStr("ts-secret")}
        | overrides
    )
    return CloudflareTurnstileVerifier(settings)


async def test_turnstile_success_and_payload(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"success": True})

    verifier = _verifier_with(monkeypatch, handler)
    assert await verifier.verify("token-123", "203.0.113.5") is True
    assert seen == {"secret": "ts-secret", "response": "token-123", "remoteip": "203.0.113.5"}


async def test_turnstile_rejected_or_error_fails_closed(monkeypatch):
    rejected = _verifier_with(monkeypatch, lambda r: httpx.Response(200, json={"success": False}))
    assert await rejected.verify("t", None) is False
    broken = _verifier_with(monkeypatch, lambda r: httpx.Response(500))
    assert await broken.verify("t", None) is False
    not_json = _verifier_with(monkeypatch, lambda r: httpx.Response(200, text="oops"))
    assert await not_json.verify("t", None) is False


async def test_turnstile_missing_token_or_secret(monkeypatch):
    verifier = _verifier_with(monkeypatch, lambda r: httpx.Response(200, json={"success": True}))
    assert await verifier.verify(None, None) is False
    no_secret = _verifier_with(
        monkeypatch,
        lambda r: httpx.Response(200, json={"success": True}),
        turnstile_secret_key=None,
    )
    assert await no_secret.verify("t", None) is False


async def test_turnstile_disabled_always_passes():
    settings = get_settings().model_copy(update={"turnstile_enabled": False})
    assert await CloudflareTurnstileVerifier(settings).verify(None, None) is True
