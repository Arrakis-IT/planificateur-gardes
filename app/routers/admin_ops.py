# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Superadmin operations: impersonation, read-only clinic view, custom holidays,
generations, audit log viewer and platform statistics (brief §4.3)."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from app.dependencies import DbSession, SuperadminAuth, require_superadmin
from app.models import AuditLog, Clinic, CustomHoliday, Generation, User
from app.repositories import admin as admin_repo
from app.repositories import clinics as clinics_repo
from app.repositories import drafts as drafts_repo
from app.repositories import users as users_repo
from app.schemas.forms import CustomHolidayForm, ReauthForm
from app.services import audit
from app.services import calendar as cal
from app.services.auth import is_user_allowed
from app.services.holidays import lu_public_holidays
from app.services.presence import hub
from app.services.security import verify_password
from app.templating import render, set_flash

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


def _redirect(url: str, message: str | None = None, level: str = "success") -> Response:
    response = RedirectResponse(url, status_code=303)
    if message:
        set_flash(response, message, level)
    return response


async def _clinic_or_404(db: DbSession, clinic_id: uuid.UUID) -> Clinic:
    clinic = await clinics_repo.get_clinic(db, clinic_id)
    if clinic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cabinet introuvable.")
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
            "Seul un médecin actif d'un cabinet actif peut être utilisé pour l'impersonation.",
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
    clinic = await _clinic_or_404(db, clinic_id)
    users = await users_repo.list_users(db, clinic_id=clinic.id)
    active = await db.scalar(
        select(Generation).where(Generation.clinic_id == clinic.id, Generation.is_active.is_(True))
    )
    return render(
        request, "admin/clinic.html", {"clinic": clinic, "users": users, "active": active}
    )


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
    """Every generation of a clinic, with archive / delete actions."""
    clinic = await _clinic_or_404(db, clinic_id)
    rows = (
        await db.execute(
            select(Generation, Clinic, User)
            .join(Clinic, Clinic.id == Generation.clinic_id)
            .outerjoin(User, User.id == Generation.generated_by)
            .where(Generation.clinic_id == clinic.id)
            .order_by(Generation.generated_at.desc())
        )
    ).all()
    return render(request, "admin/generations.html", {"rows": rows, "clinic": clinic})


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
    """Archive the active generation: the clinic is left without an active planning."""
    generation = await _generation_or_404(db, generation_id)
    target = f"/admin/clinic/{generation.clinic_id}/generations"
    if not generation.is_active:
        return _redirect(target, "Cette génération est déjà archivée.", "error")
    generation.is_active = False
    generation.archived_at = datetime.now(UTC)
    await drafts_repo.clear_drafts(db, generation.clinic_id)
    audit.record(
        db,
        "generation_archived",
        request=request,
        user_id=auth.real_user.id,
        clinic_id=generation.clinic_id,
        target_type="generation",
        target_id=generation.id,
        meta=_period(generation),
    )
    await db.commit()
    hub.publish(generation.clinic_id, "draft_cleared", {"reason": "archived", "changed": 0})
    return _redirect(target, "Génération archivée : le cabinet n'a plus de planification active.")


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
    clinic_id, was_active = generation.clinic_id, generation.is_active
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
