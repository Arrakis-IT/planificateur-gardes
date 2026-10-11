# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""First-start bootstrap: create the initial superadmin from environment variables."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.repositories import users as users_repo
from app.services import audit

logger = logging.getLogger(__name__)


async def ensure_initial_superadmin(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> bool:
    """Create the bootstrap superadmin if configured and none exists. Idempotent.

    Returns True when an account was created.
    """
    email = settings.initial_superadmin_email
    password = settings.initial_superadmin_password
    if not email or password is None or not password.get_secret_value():
        return False
    async with sessionmaker() as db:
        if await users_repo.superadmin_exists(db):
            return False
        user = await users_repo.create_user(
            db,
            email=email,
            full_name=settings.initial_superadmin_name or "Administrateur",
            password=password.get_secret_value(),
            clinic_id=None,
            is_superadmin=True,
            must_change_password=True,
        )
        audit.record(
            db,
            "user_created",
            user_id=None,
            target_type="user",
            target_id=user.id,
            meta={"email": user.email, "is_superadmin": True, "via": "bootstrap"},
        )
        await db.commit()
    logger.info("Initial superadmin created (user_id=%s)", user.id)
    return True
