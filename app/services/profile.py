# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""User profile changes shared by the settings page and the admin user sheet."""

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import audit


def rename_user(
    db: AsyncSession, user: User, full_name: str, request: Request, *, actor_id: uuid.UUID
) -> bool:
    """Change the display name (the doctor code stays stable) and audit it. Caller commits.

    Returns False when the name is unchanged (nothing recorded).
    """
    old_name = user.full_name
    if full_name == old_name:
        return False
    user.full_name = full_name
    audit.record(
        db,
        "user_renamed",
        request=request,
        user_id=actor_id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
        meta={"old": old_name, "new": full_name},
    )
    return True
