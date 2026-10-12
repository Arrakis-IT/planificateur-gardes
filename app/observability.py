# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Error reporting (Sentry) and application version.

Sentry is optional: nothing happens unless SENTRY_DSN is set. Only unhandled exceptions and
HTTP 500 responses are reported; no performance tracing, no request/log noise.

Privacy (`scrub_event`, the `before_send` hook) — health-sector users, so the policy is
"drop rather than redact" wherever a field could carry personal data:

* request: cookies, body (`data`) and query string removed; headers kept only from a short
  allow-list (no Authorization, Cookie, CSRF token, X-Forwarded-For…); reset tokens in the
  URL path (`/reset/<token>`) replaced by `[token]`.
* user: removed entirely (no id, email, username or IP).
* `server_name` removed; stack frames sent without local variables
  (`include_local_variables=False`), so form values never leak through frames.
* Remaining strings (exception messages, breadcrumbs, logentry, tags, extra) have email
  addresses and IPv4/IPv6 addresses replaced by `[email]` / `[ip]`.
"""

import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

SAFE_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "content-type",
        "content-length",
        "host",
        "user-agent",
        "hx-request",
    }
)
# Masking patterns, written to run in linear time on hostile input (no backtracking):
# * e-mail: unambiguous structure — the local part stops at "@" (not in its class) and domain
#   labels are always separated by a "." that no label class contains, so no two repetitions
#   can compete for the same characters (works without possessive quantifiers);
# * look-behinds only let a match start at the beginning of a run, so a long run without "@"
#   (or ":") is scanned once instead of once per position;
# * possessive quantifiers (`{m,n}+`, Python ≥ 3.11) where repetitions are bounded.
_EMAIL_RE = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}+\.){3}\d{1,3}+(?!\.?\d)")
# IPv6 candidates (incl. compressed "::" forms), confirmed with the ipaddress module so that
# e.g. clock times like "12:30:45" are left alone.
_IPV6_CANDIDATE_RE = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}+(?::[0-9A-Fa-f]{0,4}+){2,7}+(?![\w:])")
_RESET_PATH_RE = re.compile(r"(/reset/)[^/?#\s]++")


def app_version() -> str:
    """Release identifier: APP_VERSION / GIT_SHA env, else the VERSION file of the image."""
    for name in ("APP_VERSION", "GIT_SHA"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return value or "dev"


def _ipv6_or_text(match: re.Match[str]) -> str:
    try:
        ipaddress.IPv6Address(match.group(0))
    except ValueError:
        return match.group(0)
    return "[ip]"


def _scrub_text(value: str) -> str:
    value = _EMAIL_RE.sub("[email]", value)
    value = _IPV6_CANDIDATE_RE.sub(_ipv6_or_text, value)
    value = _IPV4_RE.sub("[ip]", value)
    return _RESET_PATH_RE.sub(r"\1[token]", value)


def _scrub(value: Any) -> Any:
    """Recursively scrub strings inside dicts / lists."""
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry `before_send`: remove personal data from an event (see module docstring)."""
    event.pop("user", None)
    event.pop("server_name", None)

    request = event.get("request")
    if isinstance(request, dict):
        for key in ("cookies", "data", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() in SAFE_HEADERS}
        if isinstance(request.get("url"), str):
            request["url"] = _scrub_text(request["url"])

    for key in ("exception", "logentry", "message", "breadcrumbs", "extra", "tags", "contexts"):
        if key in event:
            event[key] = _scrub(event[key])
    if isinstance(event.get("transaction"), str):
        event["transaction"] = _scrub_text(event["transaction"])
    return event


def init_sentry(settings: Settings) -> bool:
    """Initialise Sentry when SENTRY_DSN is configured. Returns True if enabled."""
    if settings.sentry_dsn is None or not settings.sentry_dsn.get_secret_value().strip():
        return False
    dsn = settings.sentry_dsn.get_secret_value().strip()
    if not dsn.startswith(("https://", "http://")):
        # A malformed DSN must never prevent the app from starting.
        logger.warning("SENTRY_DSN is not a valid DSN URL: Sentry disabled")
        return False

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    only_500 = {500}
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment or settings.app_env,
        release=app_version(),
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=0.0,  # no performance monitoring
        profiles_sample_rate=0.0,
        max_breadcrumbs=30,
        before_send=scrub_event,  # type: ignore[arg-type]
        integrations=[
            StarletteIntegration(failed_request_status_codes=only_500),
            FastApiIntegration(failed_request_status_codes=only_500),
            # Log records become breadcrumbs only, never events of their own.
            LoggingIntegration(level=logging.INFO, event_level=None),
        ],
    )
    logger.info("Sentry enabled (environment=%s)", settings.sentry_environment or settings.app_env)
    return True
