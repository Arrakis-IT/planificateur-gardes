# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Superadmin panel: clinics and users management (/admin/*)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from app.dependencies import (
    AppSettings,
    DbSession,
    SuperadminAuth,
    require_superadmin,
    safe_next_url,
)
from app.models import Clinic, User
from app.repositories import clinics as clinics_repo
from app.repositories import users as users_repo
from app.schemas.forms import ClinicForm, ProfileForm, UserCreateForm
from app.services import audit
from app.services.auth import is_user_allowed, revoke_user_sessions, send_invitation, set_password
from app.services.doctors import DoctorCodeError
from app.services.group_admin import get_group_admin, must_keep_group_admin, set_group_admin
from app.services.mail import MailSender, get_mail_sender
from app.services.profile import rename_user
from app.services.security import generate_temporary_password, generate_token
from app.templating import render, set_flash

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])

Mailer = Annotated[MailSender, Depends(get_mail_sender)]

SUPERADMINS_FILTER = "superadmins"
CLINICS_PAGE = "/admin/clinics"
USERS_PAGE = "/admin/users"


def _redirect(url: str, message: str | None = None, level: str = "success") -> Response:
    # Targets are built from fixed paths and typed ids; validated anyway (defense in depth).
    response = RedirectResponse(safe_next_url(url, default="/admin"), status_code=303)
    if message:
        set_flash(response, message, level)
    return response


async def _get_clinic_or_404(db: DbSession, clinic_id: uuid.UUID) -> Clinic:
    clinic = await clinics_repo.get_clinic(db, clinic_id)
    if clinic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Groupe introuvable.")
    return clinic


async def _get_user_or_404(db: DbSession, user_id: uuid.UUID) -> User:
    user = await users_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utilisateur introuvable.")
    return user


@router.get("")
async def admin_index() -> Response:
    """Admin landing → clinics."""
    return RedirectResponse(CLINICS_PAGE, status_code=303)


# --- Clinics ----------------------------------------------------------------------------


async def _render_clinics(
    request: Request, db: DbSession, extra: dict[str, Any] | None = None, status_code: int = 200
) -> Response:
    rows = await clinics_repo.list_clinics_with_user_count(db)
    return render(request, "admin/clinics.html", {"rows": rows} | (extra or {}), status_code)


@router.get("/clinics")
async def clinics_page(request: Request, db: DbSession) -> Response:
    """List clinics with their user count."""
    return await _render_clinics(request, db)


@router.post("/clinics")
async def clinic_create(
    request: Request, form: Annotated[ClinicForm, Form()], db: DbSession, auth: SuperadminAuth
) -> Response:
    """Create a clinic."""
    clinic = await clinics_repo.create_clinic(db, form.name)
    audit.record(
        db,
        "clinic_created",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
        meta={"name": clinic.name, "slug": clinic.slug},
    )
    await db.commit()
    return _redirect(CLINICS_PAGE, f"Groupe « {clinic.name} » créé.")


@router.post("/clinics/{clinic_id}/rename")
async def clinic_rename(
    request: Request,
    clinic_id: uuid.UUID,
    form: Annotated[ClinicForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Rename a clinic (the slug stays stable)."""
    clinic = await _get_clinic_or_404(db, clinic_id)
    old_name = clinic.name
    clinic.name = form.name
    audit.record(
        db,
        "clinic_renamed",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
        meta={"old": old_name, "new": form.name},
    )
    await db.commit()
    return _redirect(CLINICS_PAGE, "Groupe renommé.")


@router.post("/clinics/{clinic_id}/toggle-active")
async def clinic_toggle_active(
    request: Request, clinic_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Activate / deactivate a clinic (its users can no longer log in while inactive)."""
    clinic = await _get_clinic_or_404(db, clinic_id)
    clinic.active = not clinic.active
    audit.record(
        db,
        "clinic_activated" if clinic.active else "clinic_deactivated",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
    )
    await db.commit()
    return _redirect(CLINICS_PAGE, "Groupe réactivé." if clinic.active else "Groupe désactivé.")


@router.post("/clinics/{clinic_id}/delete")
async def clinic_delete(
    request: Request, clinic_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Delete a clinic. Refused while it still has users."""
    clinic = await _get_clinic_or_404(db, clinic_id)
    if await clinics_repo.count_clinic_users(db, clinic.id):
        return _redirect(
            CLINICS_PAGE,
            "Impossible de supprimer un groupe qui a encore des utilisateurs.",
            "error",
        )
    audit.record(
        db,
        "clinic_deleted",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
        meta={"name": clinic.name, "slug": clinic.slug},
    )
    await db.delete(clinic)
    await db.commit()
    return _redirect(CLINICS_PAGE, f"Groupe « {clinic.name} » supprimé.")


# --- Users --------------------------------------------------------------------------------


async def _render_users(
    request: Request,
    db: DbSession,
    *,
    clinic_filter: str = "",
    status_filter: str = "",
    extra: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    clinic_id: uuid.UUID | None = None
    if clinic_filter and clinic_filter != SUPERADMINS_FILTER:
        try:
            clinic_id = uuid.UUID(clinic_filter)
        except ValueError:
            clinic_filter = ""
    active = {"active": True, "inactive": False}.get(status_filter)
    users = await users_repo.list_users(
        db,
        clinic_id=clinic_id,
        superadmins_only=clinic_filter == SUPERADMINS_FILTER,
        active=active,
    )
    context = {
        "users": users,
        "clinics": await clinics_repo.list_clinics(db),
        "clinic_filter": clinic_filter,
        "status_filter": status_filter,
        "form": {},
    } | (extra or {})
    response = render(request, "admin/users.html", context, status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/users")
async def users_page(
    request: Request, db: DbSession, clinic: str = "", status: str = ""
) -> Response:
    """List users across clinics, filterable by clinic and status."""
    return await _render_users(request, db, clinic_filter=clinic, status_filter=status)


@router.post("/users")
async def user_create(
    request: Request,
    form: Annotated[UserCreateForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
    settings: AppSettings,
    mailer: Mailer,
) -> Response:
    """Create a user and email them an invitation to choose their first password."""
    form_values = {
        "email": form.email,
        "full_name": form.full_name,
        "clinic_id": form.clinic_id,
        "is_superadmin": form.is_superadmin,
    }

    async def fail(message: str) -> Response:
        return await _render_users(
            request, db, extra={"error": message, "form": form_values}, status_code=400
        )

    clinic_id: uuid.UUID | None = None
    if form.is_superadmin:
        if form.clinic_id:
            return await fail("Un superadministrateur ne peut pas être rattaché à un groupe.")
    else:
        try:
            clinic_id = uuid.UUID(form.clinic_id)
        except ValueError:
            return await fail("Choisissez un groupe.")
        if await clinics_repo.get_clinic(db, clinic_id) is None:
            return await fail("Groupe introuvable.")

    if await users_repo.get_user_by_email(db, form.email) is not None:
        return await fail("Un utilisateur avec cette adresse e-mail existe déjà.")

    try:
        user = await users_repo.create_user(
            db,
            email=form.email,
            full_name=form.full_name,
            # Random, never disclosed: the account cannot log in until the invitation is used.
            password=generate_token(),
            clinic_id=clinic_id,
            is_superadmin=form.is_superadmin,
            must_change_password=False,
        )
    except DoctorCodeError as exc:
        return await fail(str(exc))
    except IntegrityError:
        await db.rollback()
        return await fail("Un utilisateur avec cette adresse e-mail existe déjà.")

    if clinic_id is not None and await get_group_admin(db, clinic_id) is None:
        # First user of the clinic: group admin by default.
        await set_group_admin(db, clinic_id, user, actor_id=auth.real_user.id, automatic=True)
    audit.record(
        db,
        "user_created",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
        meta={
            "email": user.email,
            "doctor_code": user.doctor_code,
            "is_superadmin": user.is_superadmin,
        },
    )
    sent = await send_invitation(db, user, settings, request, mailer, actor_id=auth.real_user.id)
    await db.commit()
    created = f"Utilisateur {user.full_name} ({user.doctor_code}) créé."
    if not sent:
        return _redirect(
            USERS_PAGE,
            f"{created} L'invitation n'a pas pu être envoyée : renvoyez-la depuis sa fiche.",
            "warn",
        )
    return _redirect(USERS_PAGE, f"{created} Invitation envoyée à {user.email}.")


@router.post("/users/{user_id}/resend-invitation")
async def user_resend_invitation(
    request: Request,
    user_id: uuid.UUID,
    db: DbSession,
    auth: SuperadminAuth,
    settings: AppSettings,
    mailer: Mailer,
) -> Response:
    """Send a new invitation link (the previous one stops working)."""
    user = await _get_user_or_404(db, user_id)
    profile = f"{USERS_PAGE}/{user.id}"
    if not is_user_allowed(user):
        return _redirect(profile, "Compte ou groupe inactif : invitation non envoyée.", "error")
    sent = await send_invitation(db, user, settings, request, mailer, actor_id=auth.real_user.id)
    await db.commit()
    if not sent:
        return _redirect(
            profile, "L'invitation n'a pas pu être envoyée. Réessayez plus tard.", "error"
        )
    return _redirect(profile, f"Invitation renvoyée à {user.email}.")


@router.post("/users/{user_id}/rename")
async def user_rename(
    request: Request,
    user_id: uuid.UUID,
    form: Annotated[ProfileForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Change a user's display name (the doctor code stays stable)."""
    user = await _get_user_or_404(db, user_id)
    changed = rename_user(db, user, form.full_name, request, actor_id=auth.real_user.id)
    await db.commit()
    return _redirect(f"{USERS_PAGE}/{user.id}", "Nom modifié." if changed else None)


@router.post("/users/{user_id}/reset-password")
async def user_reset_password(
    request: Request, user_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Generate a temporary password, shown once to the admin; sessions are revoked."""
    user = await _get_user_or_404(db, user_id)
    temporary = generate_temporary_password()
    await set_password(
        db, user, temporary, request, via="admin", actor_id=auth.real_user.id, must_change=True
    )
    await db.commit()
    # Rendered directly (no redirect) so the password never travels in a cookie or URL.
    return await _render_users(
        request, db, extra={"temporary_password": {"user": user, "password": temporary}}
    )


@router.post("/users/{user_id}/toggle-active")
async def user_toggle_active(
    request: Request, user_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Activate / deactivate a user. Deactivation revokes their sessions."""
    user = await _get_user_or_404(db, user_id)
    if user.id == auth.real_user.id:
        return _redirect(USERS_PAGE, "Vous ne pouvez pas vous désactiver vous-même.", "error")
    user.active = not user.active
    if not user.active:
        await revoke_user_sessions(db, user.id)
    audit.record(
        db,
        "user_activated" if user.active else "user_deactivated",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
    )
    await db.commit()
    return _redirect(
        USERS_PAGE, "Utilisateur réactivé." if user.active else "Utilisateur désactivé."
    )


@router.post("/users/{user_id}/delete")
async def user_delete(
    request: Request, user_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Delete a user (sessions and reset tokens cascade)."""
    user = await _get_user_or_404(db, user_id)
    if user.id == auth.real_user.id:
        return _redirect(USERS_PAGE, "Vous ne pouvez pas vous supprimer vous-même.", "error")
    if await must_keep_group_admin(db, user):
        return _redirect(
            f"/admin/clinic/{user.clinic_id}",
            f"{user.full_name} est l'administrateur du groupe : désignez-en un autre avant de "
            "le supprimer.",
            "error",
        )
    audit.record(
        db,
        "user_deleted",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
        meta={"email": user.email, "doctor_code": user.doctor_code},
    )
    name = user.full_name
    await db.delete(user)
    await db.commit()
    return _redirect(USERS_PAGE, f"Utilisateur {name} supprimé.")
