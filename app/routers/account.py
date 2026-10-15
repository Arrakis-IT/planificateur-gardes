# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Account settings: profile (name), password change (also the forced-change screen) and,
for the group admin, month locks."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.config import get_settings
from app.dependencies import TERMS_ACCEPTANCE_PATH, AppSettings, AuthContext, CurrentAuth, DbSession
from app.models import User
from app.routers.auth import TERMS_REQUIRED, set_session_cookie
from app.schemas.forms import (
    ChangePasswordForm,
    MonthLockForm,
    NotificationsForm,
    ProfileForm,
    TermsForm,
)
from app.services import audit
from app.services import calendar as cal
from app.services.auth import create_session, set_password
from app.services.ical import ICAL_PATH, issue_token
from app.services.locks import LockError, apply_lock_form, can_manage_locks, lock_rows
from app.services.profile import rename_user
from app.services.security import validate_new_password, verify_password
from app.services.unsubscribe import (
    UNSUBSCRIBE_PATH,
    read_unsubscribe_token,
    set_email_notifications,
)
from app.templating import is_htmx, render, set_flash, trigger_toast

router = APIRouter(tags=["account"])

IMPERSONATION_FORBIDDEN = (
    "Impossible de changer le mot de passe d'un médecin dont vous avez pris l'identité."
)
PROFILE_IMPERSONATION_FORBIDDEN = (
    "Impossible de modifier le profil d'un médecin dont vous avez pris l'identité."
)


LOCKS_FORBIDDEN = "Réservé à l'administrateur du groupe."
SETTINGS_PAGE = "/parametres"
LOCKS_FRAGMENT = "partials/_month_locks.html"


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
        "ical": _ical_context(auth),
    }


def _ical_context(auth: AuthContext) -> dict[str, Any] | None:
    """ "Mon calendrier" section: own clinic users only (not impersonated, not read-only)."""
    user = auth.user
    if auth.impersonating or auth.read_only or user.clinic_id is None:
        return None
    base = get_settings().app_base_url.rstrip("/")
    return {"url": f"{base}{ICAL_PATH}/{user.ical_token}" if user.ical_token else None}


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
        if is_htmx(request):
            fragment = render(request, LOCKS_FRAGMENT, context, 400)
            trigger_toast(fragment, str(exc), "error")
            return fragment
        return render(request, "account/settings.html", context, 400)
    await db.commit()
    if is_htmx(request):  # swap the list in place: no reload, no scroll jump
        fragment = render(request, LOCKS_FRAGMENT, await _settings_context(auth, db))
        trigger_toast(fragment, message)
        return fragment
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


# --- Terms acceptance (users created before 0.6.0) ---------------------------------------


@router.get(TERMS_ACCEPTANCE_PATH)
async def terms_page(request: Request, auth: CurrentAuth) -> Response:
    """Blocking page: accept the CGU and privacy policy before using the service."""
    if auth.real_user.accepted_terms_at is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/accept_terms.html")


@router.post(TERMS_ACCEPTANCE_PATH)
async def accept_terms(
    request: Request, form: Annotated[TermsForm, Form()], auth: CurrentAuth, db: DbSession
) -> Response:
    """Record the (retroactive) acceptance; refused without the checkbox."""
    user = auth.real_user
    if user.accepted_terms_at is None:
        if not form.accept_terms:
            return render(request, "auth/accept_terms.html", {"error": TERMS_REQUIRED}, 400)
        user.accepted_terms_at = datetime.now(UTC)
        audit.record(
            db,
            "terms_accepted_retroactive",
            request=request,
            user_id=user.id,
            clinic_id=user.clinic_id,
            target_type="user",
            target_id=user.id,
        )
        await db.commit()
    return RedirectResponse("/", status_code=303)


# --- Notification emails preference -------------------------------------------------------

NOTIFICATIONS_IMPERSONATION_FORBIDDEN = (
    "Impossible de modifier les préférences d'un médecin dont vous avez pris l'identité."
)


@router.post(
    "/parametres/notifications",
    responses={403: {"description": NOTIFICATIONS_IMPERSONATION_FORBIDDEN}},
)
async def update_notifications(
    request: Request,
    form: Annotated[NotificationsForm, Form()],
    auth: CurrentAuth,
    db: DbSession,
) -> Response:
    """Toggle the notification emails (immediate: HTMX on checkbox change)."""
    if auth.impersonating:
        raise HTTPException(403, NOTIFICATIONS_IMPERSONATION_FORBIDDEN)
    await set_email_notifications(db, auth.user, form.enabled, source="settings", request=request)
    await db.commit()
    message = (
        "Vous recevrez les emails de notification de planification."
        if form.enabled
        else "Vous ne recevrez plus les emails de notification de planification."
    )
    if is_htmx(request):
        response = Response(status_code=204)
        trigger_toast(response, message)
        return response
    redirect = RedirectResponse(SETTINGS_PAGE, status_code=303)
    set_flash(redirect, message)
    return redirect


@router.get(f"{UNSUBSCRIBE_PATH}/{{token}}")
async def unsubscribe(
    request: Request, token: str, db: DbSession, settings: AppSettings
) -> Response:
    """One-click unsubscribe from a notification email (no login needed)."""
    user_id = read_unsubscribe_token(settings, token)
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        return render(request, "account/unsubscribe_invalid.html", status_code=400)
    await set_email_notifications(db, user, False, source="one_click_link", request=request)
    await db.commit()
    return render(request, "account/unsubscribed.html")


# --- iCal subscription link ---------------------------------------------------------------

ICAL_FORBIDDEN = "Le lien de calendrier est personnel : réservé au médecin lui-même."


@router.post(f"{SETTINGS_PAGE}/calendrier", responses={403: {"description": ICAL_FORBIDDEN}})
async def ical_link(request: Request, auth: CurrentAuth, db: DbSession) -> Response:
    """Generate the iCal feed link, or regenerate it (the previous URL stops working)."""
    if _ical_context(auth) is None:
        raise HTTPException(403, ICAL_FORBIDDEN)
    regenerated = auth.user.ical_token is not None
    await issue_token(db, auth.user, request)
    await db.commit()
    response = RedirectResponse(f"{SETTINGS_PAGE}#calendrier", status_code=303)
    set_flash(
        response,
        "Nouveau lien créé : l'ancien ne fonctionne plus, réabonnez vos calendriers."
        if regenerated
        else "Lien d'abonnement créé.",
    )
    return response
