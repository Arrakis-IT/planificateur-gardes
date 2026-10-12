# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Security headers on every response (brief §8).

Content-Security-Policy notes
-----------------------------
* No inline scripts anywhere: HTMX and app.js are self-hosted files, HTMX indicator styles
  are disabled and there are no `on*=` handlers. `script-src 'self'` is enough.
* `style-src 'self'` forbids <style> blocks (the PDF template has one, but it is rendered by
  WeasyPrint server-side and never served to browsers).
* `style-src-attr 'unsafe-inline'`: doctor colors are applied as `style="--doctor-color: …"`
  attributes. The values come from the server-side palette, never from user input.
  Generating per-doctor CSS classes would mean regenerating a stylesheet whenever a doctor
  is added or edited, and allowing inline *attributes* (not <style> blocks) has a much
  smaller attack surface: attributes cannot load resources nor contain selectors that
  exfiltrate data. Stricter alternative if ever needed: serve generated classes from an
  endpoint such as /static/doctor-colors.css?v={hash} and drop 'unsafe-inline'.
* Cloudflare Turnstile (when enabled) needs its script and iframe origin.
* `upgrade-insecure-requests` and HSTS are production only (local dev runs over HTTP).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, get_settings

SELF_SOURCE = "'self'"
TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"
HSTS = "max-age=63072000; includeSubDomains; preload"
PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=(), payment=()"


def content_security_policy(settings: Settings) -> str:
    """The CSP header value for the current configuration."""
    script_src = [SELF_SOURCE]
    directives: dict[str, list[str]] = {
        "default-src": [SELF_SOURCE],
        "script-src": script_src,
        "style-src": [SELF_SOURCE],
        "style-src-attr": ["'unsafe-inline'"],  # doctor colors, see module docstring
        "img-src": [SELF_SOURCE, "data:"],
        "font-src": [SELF_SOURCE],
        "connect-src": [SELF_SOURCE],  # HTMX requests and the SSE stream
        "form-action": [SELF_SOURCE],
        "frame-ancestors": ["'none'"],
        "base-uri": [SELF_SOURCE],
        "object-src": ["'none'"],
        "manifest-src": [SELF_SOURCE],
        "worker-src": [SELF_SOURCE],
    }
    if settings.turnstile_enabled:
        script_src.append(TURNSTILE_ORIGIN)
        directives["frame-src"] = [TURNSTILE_ORIGIN]
    policy = "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())
    if settings.is_production:
        policy += "; upgrade-insecure-requests"
    return policy


def security_headers(settings: Settings) -> dict[str, str]:
    """Headers added to every response."""
    headers = {
        "Content-Security-Policy": content_security_policy(settings),
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": PERMISSIONS_POLICY,
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if settings.is_production:
        headers["Strict-Transport-Security"] = HSTS
    return headers


class SecurityHeadersMiddleware:
    """Pure ASGI middleware (does not buffer streaming responses such as SSE)."""

    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        self.app = app
        self._headers = list(security_headers(settings or get_settings()).items())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = self._headers

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra:
                    if name not in headers:
                        headers[name] = value
                # Authenticated pages must not be stored by browsers or shared caches.
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
