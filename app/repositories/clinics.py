# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Clinic data access. Clinics are global objects: only superadmin code paths use this."""

import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinic, User

SLUG_MAX_LENGTH = 60


def slugify(name: str) -> str:
    """URL-safe slug: lowercase ASCII, words separated by dashes."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in normalized if not unicodedata.combining(c)).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-") or "cabinet"


async def _unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    existing = set(
        (await db.scalars(select(Clinic.slug).where(Clinic.slug.like(f"{base}%")))).all()
    )
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


async def list_clinics_with_user_count(db: AsyncSession) -> list[tuple[Clinic, int]]:
    """All clinics, ordered by name, with their number of users."""
    stmt = (
        select(Clinic, func.count(User.id))
        .outerjoin(User, User.clinic_id == Clinic.id)
        .group_by(Clinic.id)
        .order_by(func.lower(Clinic.name))
    )
    return [(clinic, count) for clinic, count in (await db.execute(stmt)).all()]


async def list_clinics(db: AsyncSession) -> list[Clinic]:
    """All clinics ordered by name."""
    return list((await db.scalars(select(Clinic).order_by(func.lower(Clinic.name)))).all())


async def get_clinic(db: AsyncSession, clinic_id: uuid.UUID) -> Clinic | None:
    """Clinic by id."""
    return await db.get(Clinic, clinic_id)


async def create_clinic(db: AsyncSession, name: str) -> Clinic:
    """Create a clinic with a unique slug derived from its name."""
    clinic = Clinic(name=name.strip(), slug=await _unique_slug(db, name))
    db.add(clinic)
    await db.flush()
    return clinic


async def count_clinic_users(db: AsyncSession, clinic_id: uuid.UUID) -> int:
    """Number of users (active or not) attached to the clinic."""
    stmt = select(func.count(User.id)).where(User.clinic_id == clinic_id)
    return int((await db.scalar(stmt)) or 0)
