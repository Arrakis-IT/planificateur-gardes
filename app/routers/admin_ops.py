# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Superadmin operations: impersonation, read-only clinic view, group admin and month locks,
custom holidays, generations, audit log viewer and platform statistics (brief §4.3)."""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from app.dependencies import DbSession, SuperadminAuth, require_superadmin, safe_next_url
from app.models import AuditLog, Clinic, CustomHoliday, Generation, User
from app.repositories import admin as admin_repo
from app.repositories import clinics as clinics_repo
from app.repositories import scheduling as scheduling_repo
from app.repositories import users as users_repo
from app.routers.account import PALETTE_CONFIRM_REQUIRED, appearance_context
from app.routers.appearance import (
    color_error_response,
    color_saved_response,
    palette_changed_message,
)
from app.schemas.forms import (
    CustomHolidayForm,
    DoctorColorForm,
    GroupAdminForm,
    MonthLockForm,
    PaletteForm,
    ReauthForm,
)
from app.services import audit
from app.services import calendar as cal
from app.services.auth import is_user_allowed
from app.services.group_admin import GroupAdminError, set_group_admin
from app.services.holidays import lu_public_holidays
from app.services.locks import LockError, apply_lock_form, lock_rows, locked_months
from app.services.palettes import PaletteError, change_palette, palette_of, set_doctor_color
from app.services.planning import archive_generation
from app.services.presence import hub
from app.services.security import verify_password
from app.templating import is_htmx, render, set_flash, trigger_toast

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


def _redirect(url: str, message: str | None = None, level: str = "success") -> Response:
    # Targets are built from fixed paths and typed ids; validated anyway (defense in depth).
    response = RedirectResponse(safe_next_url(url, default="/admin"), status_code=303)
    if message:
        set_flash(response, message, level)
    return response


async def _clinic_or_404(db: DbSession, clinic_id: uuid.UUID) -> Clinic:
    clinic = await clinics_repo.get_clinic(db, clinic_id)
    if clinic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Groupe introuvable.")
    return clinic


# --- Impersonation ------------------------------------------------------------------------


@router.post("/users/{user_id}/impersonate")
async def impersonate(
    request: Request, user_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Act as a clinic user: clinic pages then behave exactly as for them."""
    target = await users_repo.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utilisateur introuvable.")
    if target.is_superadmin or not is_user_allowed(target):
        return _redirect(
            "/admin/users",
            "Seul un médecin actif d'un groupe actif peut être utilisé pour l'impersonation.",
            "error",
        )
    auth.session.impersonating_user_id = target.id
    auth.session.viewing_clinic_id = None
    db.add(auth.session)
    audit.record(
        db,
        "impersonation_started",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=target.clinic_id,
        target_type="user",
        target_id=target.id,
        meta={"email": target.email},
    )
    await db.commit()
    return _redirect("/", f"Vous agissez maintenant en tant que {target.full_name}.")


@router.post("/impersonate/exit")
async def impersonate_exit(request: Request, db: DbSession, auth: SuperadminAuth) -> Response:
    """Back to the superadmin's own identity."""
    target_id = auth.session.impersonating_user_id
    if target_id is not None:
        auth.session.impersonating_user_id = None
        db.add(auth.session)
        audit.record(
            db,
            "impersonation_ended",
            user_id=auth.real_user.id,
            clinic_id=auth.user.clinic_id if auth.impersonating else None,
            target_type="user",
            target_id=target_id,
        )
        await db.commit()
    return _redirect("/admin/users", "Impersonation terminée.")


# --- Read-only clinic view ----------------------------------------------------------------


@router.get("/clinic/{clinic_id}")
async def clinic_overview(
    request: Request, clinic_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Clinic sheet: users (impersonate), read-only view, holidays, generations."""
    return await _clinic_page(request, db, await _clinic_or_404(db, clinic_id))


async def _clinic_page(
    request: Request,
    db: DbSession,
    clinic: Clinic,
    extra: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    users = await users_repo.list_users(db, clinic_id=clinic.id)
    context: dict[str, Any] = {
        "clinic": clinic,
        "users": users,
        "actives": await scheduling_repo.active_generations(db, clinic.id),
        "lock_rows": await lock_rows(db, clinic.id, cal.today_local()),
        "lock_action": f"/admin/clinic/{clinic.id}/verrous",
        "appearance": await appearance_context(
            db, clinic, f"/admin/clinic/{clinic.id}/palette", f"/admin/clinic/{clinic.id}/couleur"
        ),
    }
    context |= extra or {}
    if context.get("appearance_error") and context["appearance"]:
        context["appearance"]["error"] = context["appearance_error"]
    return render(request, "admin/clinic.html", context, status_code)


@router.post("/clinic/{clinic_id}/palette", responses={400: {"description": "Invalid palette"}})
async def clinic_palette(
    request: Request,
    clinic_id: uuid.UUID,
    form: Annotated[PaletteForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Change a clinic's palette (every doctor is recolored; confirmation required)."""
    clinic = await _clinic_or_404(db, clinic_id)
    if not form.confirm:
        return await _clinic_page(
            request, db, clinic, {"appearance_error": PALETTE_CONFIRM_REQUIRED}, 400
        )
    try:
        changed = await change_palette(
            db, clinic, form.palette, actor_id=auth.real_user.id, request=request
        )
    except PaletteError as exc:
        return await _clinic_page(request, db, clinic, {"appearance_error": str(exc)}, 400)
    await db.commit()
    return _redirect(
        f"/admin/clinic/{clinic.id}#apparence",
        palette_changed_message(clinic) if changed else None,
    )


@router.post(
    "/clinic/{clinic_id}/couleur", responses={400: {"description": "Color outside the palette"}}
)
async def clinic_doctor_color(
    request: Request,
    clinic_id: uuid.UUID,
    form: Annotated[DoctorColorForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Color of one doctor of the clinic (from the clinic sheet)."""
    clinic = await _clinic_or_404(db, clinic_id)
    doctor = (
        await users_repo.get_user_in_clinic(db, clinic.id, form.user_id) if form.user_id else None
    )
    try:
        if doctor is None:
            raise PaletteError("Médecin introuvable.")
        await set_doctor_color(
            db, clinic, doctor, form.color, actor_id=auth.real_user.id, request=request
        )
    except PaletteError as exc:
        return color_error_response(request, str(exc))
    await db.commit()
    return color_saved_response(request, doctor, f"/admin/clinic/{clinic.id}#apparence")


@router.post(
    "/users/{user_id}/color", responses={400: {"description": "Color outside the palette"}}
)
async def user_color(
    request: Request,
    user_id: uuid.UUID,
    form: Annotated[DoctorColorForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Color of a doctor, from their admin sheet."""
    doctor = await users_repo.get_user_by_id(db, user_id)
    if doctor is None or doctor.clinic_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Médecin introuvable.")
    clinic = await _clinic_or_404(db, doctor.clinic_id)
    try:
        await set_doctor_color(
            db, clinic, doctor, form.color, actor_id=auth.real_user.id, request=request
        )
    except PaletteError as exc:
        return color_error_response(request, str(exc))
    await db.commit()
    return color_saved_response(request, doctor, f"/admin/users/{doctor.id}")


@router.post("/clinic/{clinic_id}/group-admin")
async def clinic_group_admin(
    request: Request,
    clinic_id: uuid.UUID,
    form: Annotated[GroupAdminForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Hand the group admin role to another user of the clinic."""
    clinic = await _clinic_or_404(db, clinic_id)
    page = f"/admin/clinic/{clinic.id}"
    user = await users_repo.get_user_in_clinic(db, clinic.id, form.user_id)
    if user is None:
        return _redirect(page, "Utilisateur introuvable dans ce groupe.", "error")
    try:
        changed = await set_group_admin(
            db, clinic.id, user, actor_id=auth.real_user.id, request=request
        )
    except GroupAdminError as exc:
        return _redirect(page, str(exc), "error")
    await db.commit()
    return _redirect(page, f"{user.full_name} est l'administrateur du groupe." if changed else None)


@router.post("/clinic/{clinic_id}/verrous")
async def clinic_month_lock(
    request: Request,
    clinic_id: uuid.UUID,
    form: Annotated[MonthLockForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Lock / unlock a month of any clinic (same rules as the group admin)."""
    clinic = await _clinic_or_404(db, clinic_id)
    page = f"/admin/clinic/{clinic.id}"
    try:
        message = await apply_lock_form(
            db,
            clinic.id,
            form.month,
            form.action,
            cal.today_local(),
            actor_id=auth.real_user.id,
            request=request,
        )
    except LockError as exc:
        if is_htmx(request):
            fragment = await _locks_fragment(request, db, clinic, {"lock_error": str(exc)}, 400)
            trigger_toast(fragment, str(exc), "error")
            return fragment
        return await _clinic_page(request, db, clinic, {"lock_error": str(exc)}, 400)
    await db.commit()
    if is_htmx(request):  # swap the list in place: no reload, no scroll jump
        fragment = await _locks_fragment(request, db, clinic)
        trigger_toast(fragment, message)
        return fragment
    return _redirect(page, message)


async def _locks_fragment(
    request: Request,
    db: DbSession,
    clinic: Clinic,
    extra: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    context = {
        "lock_rows": await lock_rows(db, clinic.id, cal.today_local()),
        "lock_action": f"/admin/clinic/{clinic.id}/verrous",
    }
    return render(request, "partials/_month_locks.html", context | (extra or {}), status_code)


@router.post("/clinic/{clinic_id}/view")
async def clinic_view_start(
    request: Request, clinic_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Browse the clinic's pages read-only (GET only)."""
    clinic = await _clinic_or_404(db, clinic_id)
    auth.session.viewing_clinic_id = clinic.id
    auth.session.impersonating_user_id = None
    db.add(auth.session)
    audit.record(
        db,
        "clinic_view_started",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
    )
    await db.commit()
    return _redirect("/")


@router.post("/view/exit")
async def clinic_view_exit(db: DbSession, auth: SuperadminAuth) -> Response:
    """Leave the read-only view."""
    clinic_id = auth.session.viewing_clinic_id
    auth.session.viewing_clinic_id = None
    db.add(auth.session)
    await db.commit()
    return _redirect(f"/admin/clinic/{clinic_id}" if clinic_id else "/admin/clinics")


# --- Custom holidays ----------------------------------------------------------------------


@router.get("/clinic/{clinic_id}/holidays")
async def custom_holidays_page(
    request: Request, clinic_id: uuid.UUID, db: DbSession, year: int | None = None
) -> Response:
    """Clinic-specific holidays, next to the official Luxembourg ones of the year."""
    clinic = await _clinic_or_404(db, clinic_id)
    shown_year = year if year and 2000 <= year <= 2100 else cal.today_local().year
    customs = (
        await db.scalars(
            select(CustomHoliday)
            .where(CustomHoliday.clinic_id == clinic.id)
            .order_by(CustomHoliday.date)
        )
    ).all()
    return render(
        request,
        "admin/holidays.html",
        {
            "clinic": clinic,
            "customs": customs,
            "year": shown_year,
            "official": sorted(lu_public_holidays(shown_year).items()),
        },
    )


@router.post("/clinic/{clinic_id}/holidays")
async def custom_holiday_add(
    request: Request,
    clinic_id: uuid.UUID,
    form: Annotated[CustomHolidayForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Add a clinic-specific holiday."""
    clinic = await _clinic_or_404(db, clinic_id)
    target = f"/admin/clinic/{clinic.id}/holidays?year={form.date.year}"
    db.add(CustomHoliday(clinic_id=clinic.id, date=form.date, label=form.label))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return _redirect(target, "Un jour férié existe déjà à cette date.", "error")
    audit.record(
        db,
        "custom_holiday_added",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
        meta={"date": form.date.isoformat(), "label": form.label},
    )
    await db.commit()
    return _redirect(target, f"Jour férié « {form.label} » ajouté.")


@router.post("/clinic/{clinic_id}/holidays/{holiday_id}/delete")
async def custom_holiday_delete(
    request: Request,
    clinic_id: uuid.UUID,
    holiday_id: uuid.UUID,
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Remove a clinic-specific holiday."""
    holiday = await db.scalar(
        select(CustomHoliday).where(
            CustomHoliday.id == holiday_id, CustomHoliday.clinic_id == clinic_id
        )
    )
    if holiday is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jour férié introuvable.")
    audit.record(
        db,
        "custom_holiday_removed",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic_id,
        target_type="clinic",
        target_id=clinic_id,
        meta={"date": holiday.date.isoformat(), "label": holiday.label},
    )
    year = holiday.date.year
    await db.delete(holiday)
    await db.commit()
    return _redirect(f"/admin/clinic/{clinic_id}/holidays?year={year}", "Jour férié supprimé.")


# --- Generations --------------------------------------------------------------------------


@router.get("/generations")
async def generations_recent(request: Request, db: DbSession) -> Response:
    """Most recent generations across all clinics."""
    rows = (
        await db.execute(
            select(Generation, Clinic, User)
            .join(Clinic, Clinic.id == Generation.clinic_id)
            .outerjoin(User, User.id == Generation.generated_by)
            .order_by(Generation.generated_at.desc())
            .limit(100)
        )
    ).all()
    return render(request, "admin/generations.html", {"rows": rows, "clinic": None})


@router.get("/clinic/{clinic_id}/generations")
async def clinic_generations(request: Request, clinic_id: uuid.UUID, db: DbSession) -> Response:
    """Every generation of a clinic, with archive / delete actions; the active ones are
    listed first with the locked months they hold."""
    clinic = await _clinic_or_404(db, clinic_id)
    locked = await locked_months(db, clinic.id)
    active_rows = []
    for generation in await scheduling_repo.active_generations(db, clinic.id):
        held = {d.replace(day=1) for d in await scheduling_repo.assignment_dates(db, generation.id)}
        active_rows.append((generation, sorted(held & locked)))
    rows = (
        await db.execute(
            select(Generation, Clinic, User)
            .join(Clinic, Clinic.id == Generation.clinic_id)
            .outerjoin(User, User.id == Generation.generated_by)
            .where(Generation.clinic_id == clinic.id)
            .order_by(Generation.generated_at.desc())
        )
    ).all()
    return render(
        request,
        "admin/generations.html",
        {"rows": rows, "clinic": clinic, "active_rows": active_rows},
    )


async def _generation_or_404(db: DbSession, generation_id: uuid.UUID) -> Generation:
    generation = await db.get(Generation, generation_id)
    if generation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Génération introuvable.")
    return generation


def _period(generation: Generation) -> dict[str, str]:
    return {
        "period_from": generation.period_from.isoformat(),
        "period_to": generation.period_to.isoformat(),
    }


@router.post("/generations/{generation_id}/archive")
async def generation_archive(
    request: Request, generation_id: uuid.UUID, db: DbSession, auth: SuperadminAuth
) -> Response:
    """Archive one active generation (its days are shown read-only as history). There is
    no way back: its days may later be taken by another active generation."""
    generation = await _generation_or_404(db, generation_id)
    target = f"/admin/clinic/{generation.clinic_id}/generations"
    if not generation.active:
        return _redirect(target, "Cette génération est déjà archivée.", "error")
    await archive_generation(
        db, generation, reason="manual", actor_id=auth.real_user.id, request=request
    )
    await db.commit()
    hub.publish(generation.clinic_id, "draft_cleared", {"reason": "archived", "changed": 0})
    return _redirect(target, "Génération archivée (définitivement).")


@router.post("/generations/{generation_id}/delete")
async def generation_delete(
    request: Request,
    generation_id: uuid.UUID,
    form: Annotated[ReauthForm, Form()],
    db: DbSession,
    auth: SuperadminAuth,
) -> Response:
    """Delete a generation permanently; requires the superadmin's password again."""
    generation = await _generation_or_404(db, generation_id)
    if not form.password or not verify_password(auth.real_user.password_hash, form.password):
        audit.record(
            db,
            "reauth_failed",
            request=request,
            user_id=auth.real_user.id,
            clinic_id=generation.clinic_id,
            target_type="generation",
            target_id=generation.id,
            meta={"operation": "generation_delete"},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Mot de passe incorrect : suppression refusée."
        )
    clinic_id, was_active = generation.clinic_id, generation.active
    audit.record(
        db,
        "generation_deleted",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=clinic_id,
        target_type="generation",
        target_id=generation.id,
        meta=_period(generation) | {"was_active": was_active},
    )
    await db.execute(delete(Generation).where(Generation.id == generation.id))
    await db.commit()
    if was_active:
        hub.publish(clinic_id, "draft_cleared", {"reason": "deleted", "changed": 0})
    return _redirect(f"/admin/clinic/{clinic_id}/generations", "Génération supprimée.")


# --- Users --------------------------------------------------------------------------------


@router.get("/users/{user_id}")
async def user_profile(request: Request, user_id: uuid.UUID, db: DbSession) -> Response:
    """User sheet with their recent activity (as actor or as target)."""
    user = await users_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utilisateur introuvable.")
    entries = (
        await db.scalars(
            select(AuditLog)
            .where(
                or_(
                    AuditLog.user_id == user.id,
                    (AuditLog.target_type == "user") & (AuditLog.target_id == str(user.id)),
                )
            )
            .order_by(AuditLog.created_at.desc())
            .limit(30)
        )
    ).all()
    return render(
        request,
        "admin/user.html",
        {
            "user": user,
            "entries": entries,
            "users": {u.id: u for u in await users_repo.list_users(db)},
            "clinics": {c.id: c for c in await clinics_repo.list_clinics(db)},
            "palette_colors": palette_of(user.clinic).colors if user.clinic else None,
        },
    )


# --- Audit log ----------------------------------------------------------------------------


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def _day_or_none(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("/audit")
async def audit_log(
    request: Request,
    db: DbSession,
    user: str | None = None,
    clinic: str | None = None,
    action: str | None = None,
    du: str | None = None,
    au: str | None = None,
    page: int = 1,
) -> Response:
    """Paginated audit log (50 per page) with filters; dates are Luxembourg days."""
    start_day, end_day = _day_or_none(du), _day_or_none(au)
    filters = admin_repo.AuditFilters(
        user_id=_uuid_or_none(user),
        clinic_id=_uuid_or_none(clinic),
        action=action or None,
        start=datetime.combine(start_day, time.min, cal.CLINIC_TZ) if start_day else None,
        end=datetime.combine(end_day + timedelta(days=1), time.min, cal.CLINIC_TZ)
        if end_day
        else None,
    )
    page = max(page, 1)
    entries, total = await admin_repo.search_audit(db, filters, page)
    users = {u.id: u for u in await users_repo.list_users(db)}
    clinics = {c.id: c for c in await clinics_repo.list_clinics(db)}
    query = {
        k: v
        for k, v in {"user": user, "clinic": clinic, "action": action, "du": du, "au": au}.items()
        if v
    }
    return render(
        request,
        "admin/audit.html",
        {
            "entries": entries,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // admin_repo.AUDIT_PAGE_SIZE)),
            "users": users,
            "clinics": clinics,
            "actions": await admin_repo.audit_actions(db),
            "filters": {
                "user": user or "",
                "clinic": clinic or "",
                "action": action or "",
                "du": du or "",
                "au": au or "",
            },
            "query": query,
        },
    )


# --- Statistics ---------------------------------------------------------------------------


@router.get("/stats")
async def stats(request: Request, db: DbSession) -> Response:
    """Platform numbers (30-day windows where relevant)."""
    return render(
        request,
        "admin/stats.html",
        {
            "s": await admin_repo.platform_stats(db),
            "users": {u.id: u for u in await users_repo.list_users(db)},
        },
    )
