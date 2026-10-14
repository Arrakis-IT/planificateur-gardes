# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Phase 7: PWA manifest, service worker, mobile navigation."""

import json
import re

import pytest

from app.static_assets import PRECACHE, static_url, static_version
from tests.conftest import login

FONTS = {
    "/static/fonts/inter-latin.woff2",
    "/static/fonts/fraunces-latin.woff2",
    "/static/fonts/jetbrains-mono-latin.woff2",
}


async def test_manifest_content_type_and_fields(client):
    response = await client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/manifest+json"
    manifest = json.loads(response.text)
    assert manifest["display"] == "standalone" and manifest["lang"] == "fr"
    assert manifest["start_url"].startswith("/") and manifest["scope"] == "/"
    assert manifest["theme_color"] == "#2d4a3e"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])


async def test_manifest_icons_are_served(client):
    manifest = json.loads((await client.get("/manifest.webmanifest")).text)
    for icon in manifest["icons"]:
        response = await client.get(icon["src"])
        assert response.status_code == 200, icon["src"]
        assert response.headers["content-type"] == icon["type"]


async def test_service_worker_headers(client):
    response = await client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-cache"


async def test_head_requests_supported(client):
    for url in ("/manifest.webmanifest", "/sw.js"):
        assert (await client.head(url)).status_code == 200


async def test_service_worker_precaches_fonts_and_htmx(client):
    body = (await client.get("/sw.js")).text
    precache = set(json.loads(re.search(r"const PRECACHE = (\[.*?\]);", body, re.S).group(1)))
    assert all(url.endswith(f"?v={static_version()}") for url in precache)  # versioned
    paths = {url.split("?")[0] for url in precache}
    assert paths >= FONTS
    assert "/static/js/htmx.min.js" in paths
    assert {"/static/css/app.css", "/static/js/app.js", "/static/icons/favicon-32.png"} <= paths
    # Every precached file exists (a missing one would make the SW install fail).
    for url in precache:
        assert (await client.get(url)).status_code == 200, url


async def test_service_worker_never_caches_pages_or_data(client):
    body = (await client.get("/sw.js")).text
    assert 'request.method !== "GET"' in body
    assert "!VERSIONED.has(url.pathname)) return" in body  # only precached static files
    assert "/sse/" not in body and "/resultats" not in body


async def test_cache_name_tracks_static_content(client):
    body = (await client.get("/sw.js")).text
    assert f'"gardes-static-{static_version()}"' in body
    assert len(static_version()) == 12 and len(PRECACHE) == 12


async def test_pages_link_manifest_and_icons(client):
    page = await client.get("/login")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page.text
    assert 'name="theme-color"' in page.text and 'rel="apple-touch-icon"' in page.text
    assert "viewport-fit=cover" in page.text


@pytest.mark.db
async def test_tabbar_for_clinic_users_only(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("nr@ex.lu", clinic=clinic)
    await login(client, "nr@ex.lu")
    page = (await client.get("/semaine")).text
    assert 'class="tabbar"' in page
    for label in ("Aujourd'hui", "Semaine", "Mois", "Plus"):
        assert f"<span>{label}</span>" in page
    sheet = page[page.index('class="sheet"') :]
    for href in ('href="/conges"', 'href="/sur-place"', 'href="/generer"', 'action="/logout"'):
        assert href in sheet
    assert (
        'class="tab active" aria-current=page' in page
        and 'href="/semaine" class="tab active"' in page
    )


@pytest.mark.db
async def test_no_tabbar_for_superadmin_or_anonymous(client, db, make_user):
    assert 'class="tabbar"' not in (await client.get("/login")).text
    await make_user("admin@ex.lu", "Juan Admin", is_superadmin=True)
    await login(client, "admin@ex.lu")
    assert 'class="tabbar"' not in (await client.get("/admin/clinics")).text


async def test_pages_use_versioned_static_urls(client):
    """Cache busting: a deployment that changes a static file changes its URL, so the
    Cloudflare edge / browser / service worker cannot keep serving the old copy (this kept
    the 0.7.0 toast code out of production)."""
    page = (await client.get("/login")).text
    for name in ("css/app.css", "js/app.js", "js/htmx.min.js", "icons/favicon-32.png"):
        assert f'"{static_url(name)}"' in page, name
    assert '/static/js/app.js"' not in page and '/static/css/app.css"' not in page
    manifest = (await client.get("/manifest.webmanifest")).json()
    assert all("?v=" in icon["src"] for icon in manifest["icons"])


async def test_page_loads_the_toast_component(client):
    """The page ships the toast container and the script that turns HX-Trigger "toast"
    events into toasts (the versioned URL in the page serves the current file)."""
    page = (await client.get("/login")).text
    assert '<div id="toasts" class="toasts">' in page
    script = await client.get(static_url("js/app.js"))
    assert script.status_code == 200
    assert 'addEventListener("toast"' in script.text and "function showToast" in script.text
    css = (await client.get(static_url("css/app.css"))).text
    assert ".toasts {" in css and "@keyframes toast-out" in css
