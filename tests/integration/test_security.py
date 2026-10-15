# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Phase 9: security headers, CSP, admin access sweep, Sentry setup and PII scrubbing."""

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from app import main
from app.config import get_settings
from app.middleware.security_headers import content_security_policy
from app.observability import app_version, init_sentry, scrub_event
from app.routers import admin, admin_ops
from tests.conftest import TEST_CSRF_COOKIE, login, with_csrf

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"

PROD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; manifest-src 'self'; "
    "worker-src 'self'; upgrade-insecure-requests"
)
EXPECTED_PROD_HEADERS = {
    "content-security-policy": PROD_CSP,
    "strict-transport-security": "max-age=63072000; includeSubDomains; preload",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "cross-origin-opener-policy": "same-origin",
}


def _settings(**overrides: Any):
    return get_settings().model_copy(update=overrides)


def _production_settings(**overrides: Any):
    return _settings(
        app_env="production",
        app_base_url="https://gardes.arrakis.lu",
        turnstile_enabled=False,
        **overrides,
    )


async def _client_for(monkeypatch, settings) -> AsyncClient:
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    application = main.create_app()
    return AsyncClient(transport=ASGITransport(app=application), base_url="https://testserver")


@pytest.fixture
async def prod_client(monkeypatch):
    async with await _client_for(monkeypatch, _production_settings()) as client:
        yield client


# --- One test per header (production, HTML page) ------------------------------------------


@pytest.mark.parametrize(("header", "expected"), sorted(EXPECTED_PROD_HEADERS.items()))
async def test_security_header_exact_value(prod_client, header, expected):
    response = await prod_client.get("/login")
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers[header] == expected


async def test_html_is_not_cached(prod_client):
    response = await prod_client.get("/login")
    assert response.headers["cache-control"] == "no-store"
    static = await prod_client.get("/static/css/app.css")
    assert static.headers.get("cache-control") != "no-store"


@pytest.mark.parametrize("url", ["/healthz", "/sse/presence", "/static/js/app.js", "/nope"])
async def test_headers_on_every_kind_of_response(prod_client, url):
    response = await prod_client.get(url)
    for header in ("content-security-policy", "x-frame-options", "x-content-type-options"):
        assert header in response.headers, (url, header)
    assert response.headers["strict-transport-security"].startswith("max-age=63072000")


async def test_development_has_no_hsts_nor_upgrade(client):
    response = await client.get("/login")
    assert "strict-transport-security" not in response.headers
    assert "upgrade-insecure-requests" not in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_turnstile_origin_allowed_only_when_enabled():
    enabled = content_security_policy(_settings(turnstile_enabled=True))
    assert "script-src 'self' https://challenges.cloudflare.com" in enabled
    assert "frame-src https://challenges.cloudflare.com" in enabled
    disabled = content_security_policy(_settings(turnstile_enabled=False))
    assert "cloudflare" not in disabled


# --- CSP blocks inline scripts ------------------------------------------------------------


def _directive(policy: str, name: str) -> list[str]:
    for part in policy.split(";"):
        tokens = part.split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    return []


def csp_allows_inline_script(policy: str) -> bool:
    """True if a CSP lets an inline <script> without nonce/hash run."""
    sources = _directive(policy, "script-src") or _directive(policy, "default-src")
    has_nonce_or_hash = any(
        s.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-")) for s in sources
    )
    return "'unsafe-inline'" in sources and not has_nonce_or_hash


async def test_injected_inline_script_is_blocked_by_csp(monkeypatch, tmp_path):
    """A temporary template with an inline <script> is served under a CSP that blocks it."""
    (tmp_path / "injected.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        "<script>window.__pwned = true</script><p>contenu</p>{% endblock %}",
        encoding="utf-8",
    )
    from jinja2 import ChoiceLoader, FileSystemLoader

    from app.templating import render, templates

    monkeypatch.setattr(
        templates.env, "loader", ChoiceLoader([templates.env.loader, FileSystemLoader(tmp_path)])
    )
    monkeypatch.setattr(main, "get_settings", _production_settings)
    application = main.create_app()

    @application.get("/__test__/injected")
    async def injected(request: Request) -> Response:
        return render(request, "injected.html")

    async with AsyncClient(transport=ASGITransport(app=application), base_url="https://t") as c:
        response = await c.get("/__test__/injected")
    assert "<script>window.__pwned = true</script>" in response.text  # it is in the page…
    policy = response.headers["content-security-policy"]
    assert not csp_allows_inline_script(policy)  # …but the browser will not execute it
    assert _directive(policy, "script-src") == ["'self'"]
    assert _directive(policy, "style-src") == ["'self'"]  # no <style> blocks either


def test_no_inline_scripts_or_handlers_in_templates():
    """Browser-facing templates never rely on inline JS or <style> blocks."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative.startswith(("pdf/", "email/")):  # rendered server-side / in mail clients
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text):
            offenders.append(f"{relative}: inline <script>")
        if re.search(r"\son[a-z]+\s*=", text):
            offenders.append(f"{relative}: on* handler")
        if "<style" in text:
            offenders.append(f"{relative}: <style> block")
        if "javascript:" in text.lower():
            offenders.append(f"{relative}: javascript: URL")
        if re.search(r"\shx-on(:|-|=)", text):  # htmx inline handlers (eval-like, CSP-blocked)
            offenders.append(f"{relative}: hx-on handler")
    assert offenders == []


def test_only_doctor_colors_use_style_attributes():
    """style-src-attr 'unsafe-inline' exists for doctor colors only (see security_headers)."""
    for path in TEMPLATES.rglob("*.html"):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative.startswith(("pdf/", "email/")):
            continue
        for value in re.findall(r'style="([^"]*)"', path.read_text(encoding="utf-8")):
            assert value.startswith("--doctor-color: {{"), (relative, value)


# --- Admin access sweep -------------------------------------------------------------------


def _admin_routes() -> list[tuple[str, str]]:
    routes = []
    for router in (admin.router, admin_ops.router):
        for route in router.routes:
            assert isinstance(route, Route)
            for method in sorted(route.methods or ()):
                if method != "HEAD":
                    routes.append((method, route.path))
    return routes


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


def test_sweep_covers_every_admin_route():
    paths = {path for _, path in _admin_routes()}
    assert all(path.startswith("/admin") for path in paths)
    for expected in (
        "/admin/clinics",
        "/admin/users/{user_id}/impersonate",
        "/admin/impersonate/exit",
        "/admin/clinic/{clinic_id}/view",
        "/admin/generations/{generation_id}/delete",
        "/admin/audit",
        "/admin/stats",
    ):
        assert expected in paths


@pytest.mark.db
async def test_every_admin_route_forbidden_for_clinic_user(app, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@ex.lu", clinic=clinic)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        await login(c, "doc@ex.lu")
        for method, path in _admin_routes():
            data = (
                with_csrf({"password": "x", "name": "x", "label": "x"})
                if method == "POST"
                else None
            )
            response = await c.request(method, _fill(path), data=data)
            assert response.status_code == 403, (method, path, response.status_code)


@pytest.mark.db
async def test_every_admin_route_redirects_anonymous_to_login(app, db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        for method, path in _admin_routes():
            data = with_csrf() if method == "POST" else None
            response = await c.request(method, _fill(path), data=data)
            assert response.status_code == 303, (method, path)
            assert response.headers["location"].startswith("/login")


# --- Sentry -------------------------------------------------------------------------------


def test_sentry_not_initialised_without_dsn(monkeypatch):
    import sentry_sdk

    calls: list[dict] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    assert init_sentry(_settings(sentry_dsn=None)) is False
    from pydantic import SecretStr

    assert init_sentry(_settings(sentry_dsn=SecretStr("   "))) is False
    # What Docker Compose passes for `SENTRY_DSN=   # comment`: must not crash the app.
    assert init_sentry(_settings(sentry_dsn=SecretStr("# empty = Sentry disabled"))) is False
    assert calls == []
    main.create_app()  # dev / test start fine without a DSN
    assert calls == []


def test_sentry_configuration_when_enabled(monkeypatch):
    import sentry_sdk
    from pydantic import SecretStr

    calls: list[dict] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.setenv("GIT_SHA", "abc1234")
    settings = _production_settings(
        sentry_dsn=SecretStr("https://key@o1.ingest.example/1"), sentry_environment=None
    )
    assert init_sentry(settings) is True
    (kwargs,) = calls
    assert kwargs["environment"] == "production" and kwargs["release"] == "abc1234"
    assert kwargs["send_default_pii"] is False and kwargs["include_local_variables"] is False
    assert kwargs["traces_sample_rate"] == 0.0
    assert kwargs["before_send"] is scrub_event


def test_app_version_sources(monkeypatch, tmp_path):
    from app import observability

    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setattr(observability, "VERSION_FILE", tmp_path / "VERSION")
    assert app_version() == "dev"
    (tmp_path / "VERSION").write_text("deadbeef\n")
    assert app_version() == "deadbeef"
    monkeypatch.setenv("GIT_SHA", "cafe123")
    assert app_version() == "cafe123"


def test_scrub_event_removes_personal_data():
    event = {
        "user": {"id": "42", "email": "nicolas@cabinet.lu", "ip_address": "203.0.113.9"},
        "server_name": "atreides",
        "request": {
            "url": "https://gardes.arrakis.lu/reset/s3cr3t-token?x=1",
            "method": "POST",
            "query_string": "email=nicolas@cabinet.lu",
            "cookies": {"pg_session": "abc"},
            "data": {"password": "hunter2", "email": "nicolas@cabinet.lu"},
            "headers": {
                "Authorization": "Bearer abc",
                "Cookie": "pg_session=abc",
                "X-CSRF-Token": "tok",
                "X-Forwarded-For": "198.51.100.4",
                "User-Agent": "Mozilla/5.0",
            },
            "env": {"REMOTE_ADDR": "198.51.100.4"},
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": (
                        "no user nicolas@cabinet.lu from 198.51.100.4 / 2001:db8::1:2:3 at 12:30:45"
                    ),
                }
            ]
        },
        "breadcrumbs": {"values": [{"message": "Login failed for dt@cabinet.lu"}]},
        "extra": {"recipient": "mc@cabinet.lu"},
    }
    scrubbed = scrub_event(event, {})
    flat = repr(scrubbed)
    for secret in (
        "nicolas@",
        "dt@cabinet",
        "mc@cabinet",
        "203.0.113.9",
        "198.51.100.4",
        "hunter2",
        "pg_session",
        "Bearer",
        "s3cr3t-token",
        "atreides",
        "tok'",
    ):
        assert secret not in flat, secret
    assert scrubbed["request"]["headers"] == {"User-Agent": "Mozilla/5.0"}
    assert scrubbed["request"]["url"] == "https://gardes.arrakis.lu/reset/[token]?x=1"
    assert (
        scrubbed["exception"]["values"][0]["value"]
        == "no user [email] from [ip] / [ip] at 12:30:45"
    )
    assert scrubbed["breadcrumbs"]["values"][0]["message"] == "Login failed for [email]"


async def test_sentry_receives_scrubbed_unhandled_error(monkeypatch):
    """End to end with a fake transport: /_dev/sentry-test (development only) reaches Sentry."""
    import sentry_sdk
    from pydantic import SecretStr
    from sentry_sdk.transport import Transport

    captured: list[dict] = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):  # type: ignore[no-untyped-def]
            for item in envelope.items:
                if item.type == "event":
                    captured.append(item.payload.json)

    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(**kw, transport=MemoryTransport))
    settings = _settings(
        app_env="development",
        sentry_dsn=SecretStr("https://k@o1.example/1"),
        sentry_environment=None,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    try:
        application = main.create_app()
        transport = ASGITransport(app=application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            c.cookies.set("pg_session", "secret-cookie")
            response = await c.get("/_dev/sentry-test", headers={"Authorization": "Bearer x"})
        assert response.status_code == 500
        sentry_sdk.flush()
        (event,) = captured
        assert event["exception"]["values"][-1]["type"] == "ValueError"
        assert event["exception"]["values"][-1]["value"] == "Sentry test"
        assert event["environment"] == "development"
        assert "user" not in event and "cookies" not in event.get("request", {})
        assert "Authorization" not in event.get("request", {}).get("headers", {})
    finally:
        real_init()  # back to a disabled client


async def test_sentry_test_route_absent_outside_development(client):
    assert (await client.get("/_dev/sentry-test")).status_code == 404


def test_invitation_token_scrubbed_from_paths():
    from app.observability import _scrub_text

    assert _scrub_text("GET /invitation/s3cr3t?x=1") == "GET /invitation/[token]?x=1"


@pytest.mark.parametrize(
    "hostile",
    [
        "a" * 100_000,  # long local-part-like run without "@"
        "a@" + "b." * 50_000,  # many labels, no TLD at the end
        "a@" + "b" * 100_000 + "!",
        "1." * 50_000,  # IPv4-like run
        ":" * 100_000,
        "1:" * 50_000 + "z",  # IPv6-like run that never validates
        "/reset/" + "x" * 100_000,
        "/invitation/" + "x" * 100_000,
    ],
)
def test_scrubbing_regexes_run_in_linear_time(hostile):
    import time

    from app.observability import _scrub_text

    started = time.perf_counter()
    _scrub_text(hostile)
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("contact nicolas.r+gardes@cabinet-parc.lu now", "contact [email] now"),
        ("x@mail.example.museum", "[email]"),
        ("not an email: a@b", "not an email: a@b"),
        ("ip 192.168.1.10.", "ip [ip]."),
        ("version 1.2.3.4.5", "version 1.2.3.4.5"),
        ("from fe80::1 at 08:14:22", "from [ip] at 08:14:22"),
    ],
)
def test_scrubbing_regexes_still_match(text, expected):
    from app.observability import _scrub_text

    assert _scrub_text(text) == expected
