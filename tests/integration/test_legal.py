# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Public legal pages and footer links."""

import pytest

from tests.conftest import login

pytestmark = pytest.mark.db

PAGES = {
    "/mentions-legales": "Mentions légales",
    "/politique-confidentialite": "Politique de confidentialité",
    "/cgu": "Conditions générales d'utilisation",
}
FOOTER_LINKS = [f'href="{url}"' for url in PAGES]


@pytest.mark.parametrize(("url", "title"), PAGES.items())
async def test_legal_pages_are_public(client, db, url, title):
    response = await client.get(url)
    assert response.status_code == 200  # no redirect to /login
    assert f"<h1>{title}</h1>" in response.text
    assert all(link in response.text for link in FOOTER_LINKS)


async def test_legal_notice_publisher_and_host(client, db):
    text = (await client.get("/mentions-legales")).text
    for expected in (
        "Arrakis IT Services",
        "Juan Manuel Suárez",
        "16, rue du X Septembre, L-8048 Strassen, Luxembourg",
        "info@arrakis.lu",
        "https://www.arrakis.lu",
        "Hetzner Online GmbH",
        "Industriestr. 25, 91710 Gunzenhausen, Allemagne",
        "https://www.hetzner.com",
    ):
        assert expected in text, expected


async def test_privacy_and_terms_key_clauses(client, db):
    privacy = (await client.get("/politique-confidentialite")).text
    for expected in (
        "Brevo",
        "Sentry",
        "Hetzner",
        "Cloudflare",
        "CNPD",
        "pg_session",
        "portabilité",
    ):
        assert expected in privacy, expected
    terms = (await client.get("/cgu")).text
    assert "droit luxembourgeois" in terms and "tribunaux de\n    Luxembourg" in terms


async def test_footer_on_authenticated_pages(client, db, make_clinic, make_user):
    await make_user("doc@ex.lu", clinic=await make_clinic())
    await login(client, "doc@ex.lu")
    page = await client.get("/")
    assert page.status_code == 200
    assert all(link in page.text for link in FOOTER_LINKS)
