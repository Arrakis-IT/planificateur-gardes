# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Account settings: profile (name), password change (also the forced-change screen) and,
for the group admin, month locks."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.dependencies import AppSettings, AuthContext, CurrentAuth, DbSession
from app.routers.auth import set_session_cookie
from app.schemas.forms import ChangePasswordForm, MonthLockForm, ProfileForm
from app.services import calendar as cal
from app.services.auth import create_session, set_password
from app.services.locks import LockError, apply_lock_form, can_manage_locks, lock_rows
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


LOCKS_FORBIDDEN = "Réservé à l'administrateur du groupe."
SETTINGS_PAGE = "/parametres"


def _locks_clinic(auth: AuthContext) -> uuid.UUID | None:
    """Clinic whose months this user may lock from the settings page (not in read-only view)."""
    if auth.clinic is None or auth.read_only or not can_manage_locks(auth):
        return None
    return auth.clinic.id


async def _settings_context(auth: AuthContext, db: DbSession) -> dict[str, Any]:
    clinic_id = _locks_clinic(auth)
    rows = await lock_rows(db, clinic_id, cal.today_local()) if clinic_id else None
    return {
        "forced": auth.real_user.must_change_password,
        "impersonating": auth.impersonating,
        "lock_rows": rows,
        "lock_action": f"{SETTINGS_PAGE}/verrous",
    }


@router.get("/parametres")
async def settings_page(request: Request, auth: CurrentAuth, db: DbSession) -> Response:
    """Settings page: profile, password and (group admin) month locks."""
    return render(request, "account/settings.html", await _settings_context(auth, db))


@router.post(
    "/parametres/verrous",
    responses={400: {"description": "Invalid month"}, 403: {"description": LOCKS_FORBIDDEN}},
)
async def month_lock(
    request: Request, form: Annotated[MonthLockForm, Form()], auth: CurrentAuth, db: DbSession
) -> Response:
    """Lock / unlock a month of the clinic (group admin, or superadmin in a clinic context)."""
    clinic_id = _locks_clinic(auth)
    if clinic_id is None:
        raise HTTPException(403, LOCKS_FORBIDDEN)
    response = RedirectResponse(SETTINGS_PAGE, status_code=303)
    try:
        message = await apply_lock_form(
            db,
            clinic_id,
            form.month,
            form.action,
            cal.today_local(),
            actor_id=auth.real_user.id,
            request=request,
        )
    except LockError as exc:
        context = await _settings_context(auth, db) | {"lock_error": str(exc)}
        return render(request, "account/settings.html", context, 400)
    await db.commit()
    set_flash(response, message)
    return response


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
    context = await _settings_context(auth, db)
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
