# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Legal pages required to operate in Luxembourg (public, linked from the footer)."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.templating import render

router = APIRouter(tags=["legal"])

LEGAL_PAGES = {
    "/mentions-legales": "legal/mentions.html",
    "/politique-confidentialite": "legal/privacy.html",
    "/cgu": "legal/terms.html",
}


@router.get("/mentions-legales")
async def legal_notice(request: Request) -> Response:
    """Mentions légales: publisher and host."""
    return render(request, LEGAL_PAGES["/mentions-legales"])


@router.get("/politique-confidentialite")
async def privacy_policy(request: Request) -> Response:
    """Politique de confidentialité (GDPR information, cookies)."""
    return render(request, LEGAL_PAGES["/politique-confidentialite"])


@router.get("/cgu")
async def terms_of_use(request: Request) -> Response:
    """Conditions générales d'utilisation."""
    return render(request, LEGAL_PAGES["/cgu"])
