# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Mobile (touch) editing of a day in /resultats: a tap opens the edit modal, and a tap on a
cell of a locked month explains why it cannot be edited (it used to do nothing at all).

Not run in CI (needs Playwright browsers and a running app). Run it locally against the dev
stack with a clinic user whose active generation is visible in /resultats:

    E2E_BASE_URL=http://localhost:8000 E2E_EMAIL=... E2E_PASSWORD=... \\
        pytest tests/e2e -p no:cacheprovider --no-cov

The locked-cell check runs only if the page shows a locked month. WebKit (Safari's engine)
is used when installed (`playwright install webkit`).
"""

import os

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("E2E_BASE_URL")
EMAIL = os.environ.get("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD")
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

pytestmark = pytest.mark.skipif(
    not (BASE_URL and EMAIL and PASSWORD), reason="E2E_BASE_URL / E2E_EMAIL / E2E_PASSWORD unset"
)


def _launch(playwright, engine: str):
    try:
        if engine == "chromium":
            return playwright.chromium.launch(
                channel=os.environ.get("E2E_BROWSER_CHANNEL", "chrome")
            )
        return playwright.webkit.launch()
    except Exception as exc:  # browser not installed
        pytest.skip(f"{engine} unavailable: {exc}")


def _tap(page, locator) -> None:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


@pytest.fixture(scope="module")
def session_state(tmp_path_factory):
    """Log in once for the module (the login is rate limited per IP) and reuse the cookies."""
    path = tmp_path_factory.mktemp("e2e") / "state.json"
    with sync_api.sync_playwright() as p:
        browser = _launch(p, "chromium")
        page = browser.new_page()
        page.goto(f"{BASE_URL}/login")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        with page.expect_navigation():
            page.click("button[type=submit]")
        assert "/login" not in page.url, "login failed (rate limited? wait a minute)"
        page.context.storage_state(path=str(path))
        browser.close()
    return str(path)


def _mobile_page(p, engine: str, state: str):
    browser = _launch(p, engine)
    context = browser.new_context(
        viewport={"width": 375, "height": 812},
        has_touch=True,
        is_mobile=engine == "chromium",  # WebKit does not support is_mobile
        user_agent=IPHONE_UA,
        # The PWA service worker would hide requests from page.route() (WebKit).
        service_workers="block",
        storage_state=state,
    )
    page = context.new_page()
    violations: list[str] = []
    page.on(
        "console",
        lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None,
    )
    return browser, page, violations


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_tap_on_a_day_opens_the_edit_modal(engine, session_state):
    with sync_api.sync_playwright() as p:
        browser, page, violations = _mobile_page(p, engine, session_state)

        page.goto(f"{BASE_URL}/resultats")
        _tap(page, page.locator("a.month-cell.clickable").first)
        modal = page.locator("#modal")
        modal.get_by_text("Modifier l'affectation").wait_for(timeout=5000)
        assert modal.get_by_text("Modifier l'affectation").is_visible()
        page.get_by_role("button", name="Fermer").tap()

        locked = page.locator("button.month-cell.locked-cell")
        if locked.count():
            _tap(page, locked.first)
            toast = page.locator("#toasts .toast", has_text="verrouillé")
            toast.wait_for(timeout=3000)
            assert "aucune modification possible" in toast.inner_text()
            assert not page.inner_html("#modal").strip()  # no edit modal for a locked day
        assert violations == []  # no CSP violation from the app's own pages
        browser.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_failed_modal_request_falls_back_to_the_full_page(engine, session_state):
    """If the HTMX request fails (e.g. a CDN challenge answers 403), the tap still leads to
    the day's edit page instead of doing nothing."""
    with sync_api.sync_playwright() as p:
        browser, page, _ = _mobile_page(p, engine, session_state)
        page.goto(f"{BASE_URL}/resultats")

        def challenge(route):
            if route.request.headers.get("hx-request") == "true":
                route.fulfill(status=403, body="challenge", content_type="text/html")
            else:
                route.continue_()

        page.route("**/resultats/jour/**", challenge)
        cell = page.locator("a.month-cell.clickable").first
        href = cell.get_attribute("href")
        with page.expect_navigation(timeout=5000):
            _tap(page, cell)
        assert page.url.endswith(href)
        assert page.get_by_text("Modifier l'affectation").first.is_visible()
        browser.close()
