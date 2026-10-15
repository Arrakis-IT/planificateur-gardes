# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Response helpers shared by the settings and admin routes of the doctor color palette."""

from fastapi.responses import RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from app.models import Clinic, User
from app.services.palettes import palette_of
from app.templating import is_htmx, render, set_flash, trigger_toast


def palette_changed_message(clinic: Clinic) -> str:
    """Toast after a palette change."""
    return f"Palette « {palette_of(clinic).label} » appliquée : couleurs des médecins réattribuées."


def color_saved_response(request: Request, doctor: User, redirect_to: str) -> Response:
    """204 + toast for HTMX, redirect + flash otherwise."""
    message = f"Couleur de {doctor.full_name} enregistrée."
    if is_htmx(request):
        response = Response(status_code=204)
        trigger_toast(response, message)
        return response
    redirect = RedirectResponse(redirect_to, status_code=303)
    set_flash(redirect, message)
    return redirect


def color_error_response(request: Request, message: str) -> Response:
    """400 with an error toast (HTMX) or an error page."""
    if is_htmx(request):
        response = Response(status_code=400)
        trigger_toast(response, message, "error")
        return response
    return render(request, "errors/error.html", {"status_code": 400, "message": message}, 400)
