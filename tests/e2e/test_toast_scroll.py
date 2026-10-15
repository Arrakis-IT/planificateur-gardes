# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Visual check (real browser): locking a month shows a toast without moving the page.

Not run in CI: needs Playwright with a Chromium-based browser and a running app with a group
admin whose active generation covers the current month. Run it locally against the dev stack:

    E2E_BASE_URL=http://localhost:8000 E2E_EMAIL=... E2E_PASSWORD=... \\
        pytest tests/e2e -p no:cacheprovider --no-cov

The scroll position cannot be observed from the in-process HTTP tests; those check the
HTMX contract instead (fragment + HX-Trigger toast, see tests/integration/test_toasts.py).
"""

import os

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("E2E_BASE_URL")
EMAIL = os.environ.get("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD")

pytestmark = pytest.mark.skipif(
    not (BASE_URL and EMAIL and PASSWORD), reason="E2E_BASE_URL / E2E_EMAIL / E2E_PASSWORD unset"
)


def test_lock_toast_keeps_scroll_position():
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("E2E_BROWSER_CHANNEL", "chrome"))
        page = browser.new_page(viewport={"width": 1280, "height": 600})
        page.goto(f"{BASE_URL}/login")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")

        page.goto(f"{BASE_URL}/parametres")
        page.wait_for_load_state("load")
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        for _ in range(20):  # wait_for_function(str) is eval: blocked by the app's CSP
            if page.evaluate("window.scrollY") > 0:
                break
            page.wait_for_timeout(100)
        before = page.evaluate("window.scrollY")
        assert before > 0, "the settings page must be scrollable for this check"

        # Two quick clicks: two stacked toasts, no reload, no scroll jump.
        for _ in range(2):
            page.locator("#month-locks button[value=lock]").first.click()
            page.wait_for_timeout(400)
        toasts = page.locator("#toasts .toast")
        assert toasts.count() == 2
        assert "verrouillé" in toasts.first.inner_text()
        assert page.evaluate("window.scrollY") == before

        # Auto-dismiss (3.5 s + fade-out), still without moving the page.
        page.wait_for_timeout(4500)
        assert toasts.count() == 0
        assert page.evaluate("window.scrollY") == before

        # Restore: unlock what was locked.
        for _ in range(2):
            page.locator("#month-locks button[value=unlock]").first.click()
            page.wait_for_timeout(400)
        browser.close()
