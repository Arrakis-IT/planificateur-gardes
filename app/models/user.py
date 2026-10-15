# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""User: one user per doctor; superadmins have no clinic."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, false, true
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.clinic import Clinic


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An authenticated user. Clinic users are doctors; superadmins operate the platform."""

    __tablename__ = "user"
    __table_args__ = (
        # Superadmins live outside any clinic; everybody else belongs to exactly one.
        CheckConstraint(
            "(is_superadmin AND clinic_id IS NULL) "
            "OR (NOT is_superadmin AND clinic_id IS NOT NULL)",
            name="superadmin_has_no_clinic",
        ),
        Index(
            "uq_user_clinic_id_doctor_code",
            "clinic_id",
            "doctor_code",
            unique=True,
            postgresql_where="clinic_id IS NOT NULL",
        ),
        # At most one group admin per clinic.
        Index(
            "uq_user_clinic_id_group_admin",
            "clinic_id",
            unique=True,
            postgresql_where="is_group_admin",
        ),
    )

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    doctor_code: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str] = mapped_column(Text, nullable=False)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # Group admin of their clinic: locks / unlocks months (see app/services/locks.py).
    is_group_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the user accepted the CGU and privacy policy (checkbox of the invitation form).
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Notification emails (schedule generated / edited). Invitations and password resets are
    # always sent. See app/services/unsubscribe.py.
    email_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true()
    )
    # Secret of the personal iCal feed URL (/ical/{token}), created on demand from the
    # settings page. Stored in clear: the page shows the subscription URL again.
    ical_token: Mapped[str | None] = mapped_column(Text, unique=True)

    # Loaded eagerly: async sessions cannot lazy-load on attribute access.
    clinic: Mapped[Clinic | None] = relationship(lazy="selectin")
