# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Initial superadmin bootstrap."""

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import User
from app.services.bootstrap import ensure_initial_superadmin

pytestmark = pytest.mark.db


def _settings(email: str | None, password: str | None):
    return get_settings().model_copy(
        update={
            "initial_superadmin_email": email,
            "initial_superadmin_password": SecretStr(password) if password else None,
            "initial_superadmin_name": "Juan Admin",
        }
    )


async def test_bootstrap_creates_superadmin_once(db):
    settings = _settings("root@ex.lu", "bootstrap-password-1")
    assert await ensure_initial_superadmin(get_sessionmaker(), settings) is True
    assert await ensure_initial_superadmin(get_sessionmaker(), settings) is False
    users = (await db.scalars(select(User))).all()
    assert len(users) == 1
    admin = users[0]
    assert admin.is_superadmin and admin.clinic_id is None and admin.must_change_password


async def test_bootstrap_skipped_without_config(db):
    assert await ensure_initial_superadmin(get_sessionmaker(), _settings(None, None)) is False
    assert await db.scalar(select(func.count(User.id))) == 0
