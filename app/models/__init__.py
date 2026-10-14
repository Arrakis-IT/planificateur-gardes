# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""SQLAlchemy models. Import every model here so Alembic sees the full metadata."""

from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.clinic import Clinic
from app.models.password_reset_token import PasswordResetToken
from app.models.scheduling import (
    Assignment,
    AssignmentDraft,
    CustomHoliday,
    Generation,
    Holiday,
    MonthLock,
    OnsiteDay,
)
from app.models.session import UserSession
from app.models.user import User

__all__ = [
    "Assignment",
    "AssignmentDraft",
    "AuditLog",
    "Base",
    "Clinic",
    "CustomHoliday",
    "Generation",
    "Holiday",
    "MonthLock",
    "OnsiteDay",
    "PasswordResetToken",
    "User",
    "UserSession",
]
