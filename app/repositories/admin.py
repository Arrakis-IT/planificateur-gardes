# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Cross-clinic queries for the superadmin panel (audit search, platform statistics)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Clinic, Generation, User

AUDIT_PAGE_SIZE = 50


@dataclass(frozen=True)
class AuditFilters:
    """Audit viewer filters (all optional). Dates are UTC bounds [start, end)."""

    user_id: uuid.UUID | None = None
    clinic_id: uuid.UUID | None = None
    action: str | None = None
    start: datetime | None = None
    end: datetime | None = None


def _filtered(stmt: Select[Any], filters: AuditFilters) -> Select[Any]:
    if filters.user_id is not None:
        stmt = stmt.where(AuditLog.user_id == filters.user_id)
    if filters.clinic_id is not None:
        stmt = stmt.where(AuditLog.clinic_id == filters.clinic_id)
    if filters.action:
        stmt = stmt.where(AuditLog.action == filters.action)
    if filters.start is not None:
        stmt = stmt.where(AuditLog.created_at >= filters.start)
    if filters.end is not None:
        stmt = stmt.where(AuditLog.created_at < filters.end)
    return stmt


async def search_audit(
    db: AsyncSession, filters: AuditFilters, page: int
) -> tuple[list[AuditLog], int]:
    """One page of audit entries (newest first) and the total number of matches."""
    total = int(await db.scalar(_filtered(select(func.count(AuditLog.id)), filters)) or 0)
    stmt = (
        _filtered(select(AuditLog), filters)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * AUDIT_PAGE_SIZE)
        .limit(AUDIT_PAGE_SIZE)
    )
    return list((await db.scalars(stmt)).all()), total


async def audit_actions(db: AsyncSession) -> list[str]:
    """Distinct actions present in the log (for the filter dropdown)."""
    return list(
        (await db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action))).all()
    )


@dataclass(frozen=True)
class PlatformStats:
    """Numbers shown on /admin/stats."""

    clinics_active: int
    clinics_inactive: int
    users_active: int
    users_inactive: int
    superadmins: int
    generations_by_clinic: list[tuple[Clinic, int]]
    emails_sent: int
    emails_failed: int
    failed_logins: list[AuditLog]
    email_errors: list[tuple[AuditLog, dict[str, Any]]]


async def platform_stats(db: AsyncSession, now: datetime | None = None) -> PlatformStats:
    """Platform-wide statistics over the last 30 days where relevant."""
    since = (now or datetime.now(UTC)) - timedelta(days=30)
    clinic_counts = dict(
        (
            await db.execute(select(Clinic.active, func.count(Clinic.id)).group_by(Clinic.active))
        ).all()
    )
    user_rows = (
        await db.execute(
            select(User.is_superadmin, User.active, func.count(User.id)).group_by(
                User.is_superadmin, User.active
            )
        )
    ).all()
    clinic_users = {active: n for superadmin, active, n in user_rows if not superadmin}
    superadmins = sum(n for superadmin, _, n in user_rows if superadmin)

    generations = (
        await db.execute(
            select(Clinic, func.count(Generation.id))
            .join(Generation, Generation.clinic_id == Clinic.id)
            .where(Generation.generated_at >= since)
            .group_by(Clinic.id)
            .order_by(func.count(Generation.id).desc(), Clinic.name)
        )
    ).all()

    sends = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "pdf_sent", AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc())
        )
    ).all()
    emails_sent = sum(int((e.meta or {}).get("sent", 0)) for e in sends)
    emails_failed = sum(int((e.meta or {}).get("failed", 0)) for e in sends)
    email_errors: list[tuple[AuditLog, dict[str, Any]]] = []
    for entry in sends:
        meta = entry.meta or {}
        if meta.get("error"):
            email_errors.append((entry, {"email": "—", "message": meta["error"]}))
        email_errors.extend((entry, r) for r in meta.get("recipients", []) if not r.get("ok", True))

    failed_logins = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "login_failed")
            .order_by(AuditLog.created_at.desc())
            .limit(10)
        )
    ).all()
    return PlatformStats(
        clinics_active=int(clinic_counts.get(True, 0)),
        clinics_inactive=int(clinic_counts.get(False, 0)),
        users_active=int(clinic_users.get(True, 0)),
        users_inactive=int(clinic_users.get(False, 0)),
        superadmins=int(superadmins),
        generations_by_clinic=[(clinic, int(n)) for clinic, n in generations],
        emails_sent=emails_sent,
        emails_failed=emails_failed,
        failed_logins=list(failed_logins),
        email_errors=email_errors[:10],
    )
