# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Account settings: profile (name) and password change (also the forced-change screen)."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.dependencies import AppSettings, CurrentAuth, DbSession
from app.routers.auth import set_session_cookie
from app.schemas.forms import ChangePasswordForm, ProfileForm
from app.services.auth import create_session, set_password
from app.services.profile import rename_user
from app.services.security import validate_new_password, verify_password
from app.templating import render, set_flash

router = APIRouter(tags=["account"])

IMPERSONATION_FORBIDDEN = (
    "Impossible de changer le mot de passe d'un médecin dont vous avez pris l'identité."
)
PROFILE_IMPERSONATION_FORBIDDEN = (
    "Impossible de modifier le profil d'un médecin dont vous avez pris l'identité."
)


@router.get("/parametres")
async def settings_page(request: Request, auth: CurrentAuth) -> Response:
    """Settings page with the password change form."""
    return render(
        request,
        "account/settings.html",
        {"forced": auth.real_user.must_change_password, "impersonating": auth.impersonating},
    )


@router.post(
    "/parametres/profil", responses={403: {"description": PROFILE_IMPERSONATION_FORBIDDEN}}
)
async def update_profile(
    request: Request, form: Annotated[ProfileForm, Form()], auth: CurrentAuth, db: DbSession
) -> Response:
    """Change the connected user's display name."""
    if auth.impersonating:
        raise HTTPException(403, PROFILE_IMPERSONATION_FORBIDDEN)
    changed = rename_user(db, auth.user, form.full_name, request, actor_id=auth.user.id)
    await db.commit()
    response = RedirectResponse("/parametres", status_code=303)
    if changed:
        set_flash(response, "Votre nom a été modifié.")
    return response


@router.post("/parametres/mot-de-passe", responses={403: {"description": IMPERSONATION_FORBIDDEN}})
async def change_password(
    request: Request,
    form: Annotated[ChangePasswordForm, Form()],
    auth: CurrentAuth,
    db: DbSession,
    settings: AppSettings,
) -> Response:
    """Change the password, revoke every session and open a fresh one."""
    if auth.impersonating:
        raise HTTPException(403, IMPERSONATION_FORBIDDEN)
    user = auth.user
    context = {"forced": user.must_change_password}
    error: str | None = None
    if not verify_password(user.password_hash, form.current_password):
        error = "Le mot de passe actuel est incorrect."
    elif form.password == form.current_password:
        error = "Le nouveau mot de passe doit être différent de l'actuel."
    else:
        error = validate_new_password(form.password, form.password_confirm, user.email)
    if error:
        return render(request, "account/settings.html", context | {"error": error}, 400)

    await set_password(db, user, form.password, request, via="self")
    cookie = await create_session(db, user, settings, request)
    await db.commit()

    response = RedirectResponse("/admin" if user.is_superadmin else "/", status_code=303)
    set_session_cookie(response, cookie, settings)
    set_flash(response, "Votre mot de passe a été modifié.")
    return response
