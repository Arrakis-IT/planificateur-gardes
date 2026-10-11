# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""CSRF protection: signed double-submit cookie (OWASP).

- `CSRFCookieMiddleware` makes sure every browser holds a random `csrftoken` cookie
  (HttpOnly, SameSite=Lax) and exposes it to the request as `request.state.csrf_cookie`.
- Forms embed `HMAC(secret, cookie)` in a hidden `csrf_token` field (HTMX: `X-CSRF-Token`).
- `verify_csrf` (global dependency) rejects unsafe methods whose submitted token does not
  match the HMAC of the cookie. An attacker can neither read the cookie nor compute the
  HMAC without APP_SECRET_KEY, so cookie injection alone is not enough.

Chosen over `fastapi-csrf-protect` (the brief allows an equivalent) to keep the mechanism
small, dependency-free and fully covered by our own tests.
"""

import hashlib
import hmac
import re

from fastapi import HTTPException, Request, status
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.services.security import derive_key, generate_token

CSRF_COOKIE_NAME = "csrftoken"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def form_token(cookie_token: str) -> str:
    """Token to embed in forms for a given cookie value."""
    key = derive_key(get_settings().app_secret_key.get_secret_value(), "csrf")
    return hmac.new(key, cookie_token.encode(), hashlib.sha256).hexdigest()


def csrf_token_for(request: Request) -> str:
    """Form token for the current request (used by templates)."""
    return form_token(request.state.csrf_cookie)


class CSRFCookieMiddleware:
    """Pure ASGI middleware issuing the CSRF cookie when missing or malformed."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cookie = HTTPConnection(scope).cookies.get(CSRF_COOKIE_NAME)
        is_new = cookie is None or not _TOKEN_RE.match(cookie)
        token = generate_token() if is_new else cookie
        scope.setdefault("state", {})["csrf_cookie"] = token

        if not is_new:
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        helper = Response()
        helper.set_cookie(
            CSRF_COOKIE_NAME,
            str(token),
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
            path="/",
        )
        set_cookie = helper.headers["set-cookie"]

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("set-cookie", set_cookie)
            await send(message)

        await self.app(scope, receive, send_with_cookie)


async def verify_csrf(request: Request) -> None:
    """Global dependency: reject state-changing requests without a valid CSRF token."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    submitted = request.headers.get(CSRF_HEADER)
    if submitted is None:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            value = (await request.form()).get(CSRF_FORM_FIELD)
            submitted = value if isinstance(value, str) else None
    if not cookie or not submitted or not hmac.compare_digest(submitted, form_token(cookie)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Jeton de sécurité invalide.")
