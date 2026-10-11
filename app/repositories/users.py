# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""User data access.

Functions named `*_in_clinic` / `list_clinic_*` are the clinic-scoped API used by the
user-facing app: they always filter by `clinic_id`. Global lookups (by email / id, list
across clinics) are reserved to authentication and superadmin code paths.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.doctors import generate_doctor_code, pick_color
from app.services.security import hash_password

# --- Clinic-scoped ----------------------------------------------------------------


async def list_clinic_users(
    db: AsyncSession, clinic_id: uuid.UUID, *, active_only: bool = True
) -> list[User]:
    """Users of one clinic, ordered by name."""
    stmt = select(User).where(User.clinic_id == clinic_id)
    if active_only:
        stmt = stmt.where(User.active.is_(True))
    return list((await db.scalars(stmt.order_by(func.lower(User.full_name)))).all())


async def get_user_in_clinic(
    db: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    """A user only if it belongs to `clinic_id`; None otherwise (never leaks other clinics)."""
    stmt = select(User).where(User.id == user_id, User.clinic_id == clinic_id)
    return (await db.scalars(stmt)).one_or_none()


# --- Global (auth / superadmin) ------------------------------------------------------


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """User by email (case-insensitive: the column is CITEXT)."""
    return (await db.scalars(select(User).where(User.email == email.strip()))).one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """User by id, any clinic."""
    return await db.get(User, user_id)


async def list_users(
    db: AsyncSession,
    *,
    clinic_id: uuid.UUID | None = None,
    superadmins_only: bool = False,
    active: bool | None = None,
) -> list[User]:
    """All users across clinics with optional filters (superadmin views)."""
    stmt = select(User)
    if superadmins_only:
        stmt = stmt.where(User.is_superadmin.is_(True))
    elif clinic_id is not None:
        stmt = stmt.where(User.clinic_id == clinic_id)
    if active is not None:
        stmt = stmt.where(User.active.is_(active))
    stmt = stmt.order_by(User.is_superadmin.desc(), func.lower(User.full_name))
    return list((await db.scalars(stmt)).all())


async def superadmin_exists(db: AsyncSession) -> bool:
    """True if at least one superadmin account exists."""
    stmt = select(func.count(User.id)).where(User.is_superadmin.is_(True))
    return bool(await db.scalar(stmt))


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str,
    clinic_id: uuid.UUID | None,
    is_superadmin: bool = False,
    must_change_password: bool = True,
) -> User:
    """Create a user with a doctor code and color unique within its clinic."""
    taken_codes: list[str] = []
    used_colors: list[str] = []
    if clinic_id is not None:
        rows = (
            await db.execute(
                select(User.doctor_code, User.color, User.active).where(User.clinic_id == clinic_id)
            )
        ).all()
        taken_codes = [code for code, _, _ in rows]
        used_colors = [color for _, color, active in rows if active]
    user = User(
        email=email.strip(),
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        doctor_code=generate_doctor_code(full_name, taken_codes),
        color=pick_color(used_colors),
        clinic_id=None if is_superadmin else clinic_id,
        is_superadmin=is_superadmin,
        must_change_password=must_change_password,
    )
    db.add(user)
    await db.flush()
    return user
