# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Group admin role: one user per clinic (unique partial index on user.is_group_admin).

The first user created in a clinic gets the role; a superadmin can hand it to another
user of the clinic at any time (the previous one loses it). A group admin cannot be
deleted while the clinic has other users: the superadmin must appoint another first.
"""

import uuid

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import audit


class GroupAdminError(ValueError):
    """The role change is invalid (French message for the user)."""


async def get_group_admin(db: AsyncSession, clinic_id: uuid.UUID) -> User | None:
    """The clinic's group admin, if any."""
    stmt = select(User).where(User.clinic_id == clinic_id, User.is_group_admin.is_(True))
    return (await db.scalars(stmt)).one_or_none()


async def set_group_admin(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    user: User,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
    automatic: bool = False,
) -> bool:
    """Make `user` the group admin of the clinic. Returns False if they already are.

    Caller commits.
    """
    if user.clinic_id != clinic_id or user.is_superadmin:
        raise GroupAdminError("Cet utilisateur n'appartient pas à ce groupe.")
    if not user.active:
        raise GroupAdminError("Un utilisateur inactif ne peut pas être administrateur du groupe.")
    previous = await get_group_admin(db, clinic_id)
    if previous is not None and previous.id == user.id:
        return False
    if previous is not None:
        previous.is_group_admin = False
        await db.flush()  # free the unique slot before granting it
    user.is_group_admin = True
    audit.record(
        db,
        "group_admin_changed",
        request=request,
        user_id=actor_id,
        clinic_id=clinic_id,
        target_type="user",
        target_id=user.id,
        meta={
            "previous": str(previous.id) if previous else None,
            "previous_name": previous.full_name if previous else None,
            "new_name": user.full_name,
            "automatic": automatic,
        },
    )
    await db.flush()
    return True


async def must_keep_group_admin(db: AsyncSession, user: User) -> bool:
    """True when `user` is a group admin whose clinic has other users (no deletion yet)."""
    if not user.is_group_admin or user.clinic_id is None:
        return False
    others = await db.scalar(
        select(func.count(User.id)).where(User.clinic_id == user.clinic_id, User.id != user.id)
    )
    return bool(others)
