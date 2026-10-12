# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Jinja2 environments (web pages and emails) and rendering helpers, incl. flash messages."""

import json
import uuid
from pathlib import Path
from typing import Any

import jinja2
from fastapi import Request
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from markupsafe import Markup
from starlette.responses import Response

from app.config import get_settings
from app.middleware.csrf import CSRF_FORM_FIELD, csrf_token_for
from app.services import calendar as cal
from app.services.security import derive_key
from app.services.turnstile import FORM_FIELD as TURNSTILE_FIELD

TEMPLATES_DIR = Path(__file__).parent / "templates"
FLASH_COOKIE_NAME = "flash"
FLASH_MAX_AGE_SECONDS = 60

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Emails: HTML parts autoescaped, plain-text parts rendered verbatim.
# NOTE: autoescape scoped to HTML on purpose. The email/ folder also contains
# plain-text (.txt) templates that must NOT escape HTML entities. The current
# config correctly enables escape only for .html. Acknowledged in Aikido
# (Jinja2 XSS rule) as a scoped, intentional configuration.
email_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_DIR / "email"),
    autoescape=jinja2.select_autoescape(enabled_extensions=("html",), default=False),
    undefined=jinja2.StrictUndefined,
)


def csrf_input(request: Request) -> Markup:
    """Hidden form field carrying the CSRF token."""
    token = csrf_token_for(request)
    return Markup('<input type="hidden" name="{}" value="{}">').format(CSRF_FORM_FIELD, token)


templates.env.globals["csrf_input"] = csrf_input
templates.env.globals["csrf_token"] = csrf_token_for
templates.env.globals["turnstile_field"] = TURNSTILE_FIELD
templates.env.globals["WEEKDAYS_SHORT_FR"] = cal.WEEKDAYS_SHORT_FR
templates.env.globals["month_weeks"] = cal.month_weeks
templates.env.filters["long_date"] = cal.long_date
templates.env.filters["short_date"] = cal.short_date
templates.env.filters["local_datetime"] = cal.local_datetime
templates.env.filters["month_label"] = cal.month_label
templates.env.filters["weekday_fr"] = lambda d: cal.WEEKDAYS_FR[d.weekday()]


def _as_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def pretty_json(value: Any) -> str:
    """Indented JSON keeping accents (autoescaped by the template)."""
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


_BROWSERS = (
    ("Edg/", "Edge"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("Android", "Android"),
    ("Mac OS X", "macOS"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
)


def short_user_agent(value: str | None) -> str:
    """ "Chrome · macOS"-style summary of a User-Agent header."""
    if not value:
        return "—"
    browser = next((name for token, name in _BROWSERS if token in value), None)
    system = next((name for token, name in _SYSTEMS if token in value), None)
    summary = " · ".join(part for part in (browser, system) if part)
    return summary or value[:30]


templates.env.filters["pretty_json"] = pretty_json
templates.env.filters["short_ua"] = short_user_agent
templates.env.filters["uuid"] = lambda value: _as_uuid(value)


# --- Flash messages (one-shot, signed cookie; never put secrets in them) -----------------


def _flash_serializer() -> URLSafeTimedSerializer:
    key = derive_key(get_settings().app_secret_key.get_secret_value(), "flash")
    return URLSafeTimedSerializer(key, salt="flash")


def set_flash(response: Response, message: str, level: str = "success") -> None:
    """Attach a one-shot message displayed on the next rendered page."""
    settings = get_settings()
    response.set_cookie(
        FLASH_COOKIE_NAME,
        _flash_serializer().dumps({"m": message, "l": level}),
        max_age=FLASH_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _read_flash(request: Request) -> dict[str, str] | None:
    raw = request.cookies.get(FLASH_COOKIE_NAME)
    if not raw:
        return None
    try:
        data = _flash_serializer().loads(raw, max_age=FLASH_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    if isinstance(data, dict) and isinstance(data.get("m"), str):
        return {"message": data["m"], "level": str(data.get("l", "success"))}
    return None


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """Render a page template, consuming any pending flash message."""
    settings = get_settings()
    ctx: dict[str, Any] = {
        "flash": _read_flash(request),
        "auth": getattr(request.state, "auth", None),
        "turnstile_enabled": settings.turnstile_enabled,
        "turnstile_site_key": settings.turnstile_site_key,
    }
    ctx.update(context or {})
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    if FLASH_COOKIE_NAME in request.cookies:
        response.delete_cookie(FLASH_COOKIE_NAME, path="/")
    return response


def render_email(name: str, context: dict[str, Any]) -> str:
    """Render an email template (from templates/email/)."""
    return email_env.get_template(name).render(**context)
