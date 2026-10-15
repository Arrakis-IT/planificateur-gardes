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


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_tap_on_a_day_opens_the_edit_modal(engine):
    with sync_api.sync_playwright() as p:
        browser = _launch(p, engine)
        context = browser.new_context(
            viewport={"width": 375, "height": 812},
            has_touch=True,
            is_mobile=engine == "chromium",  # WebKit does not support is_mobile
            user_agent=IPHONE_UA,
        )
        page = context.new_page()
        page.goto(f"{BASE_URL}/login")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        with page.expect_navigation():
            page.tap("button[type=submit]")

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
        browser.close()
