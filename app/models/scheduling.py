# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Scheduling data: doctor vacations, on-site days, custom holidays, generations."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class Holiday(UUIDPrimaryKeyMixin, Base):
    """A vacation day of a doctor (table name from the brief: `holiday`).

    Not to be confused with public holidays (computed, see app/services/holidays.py)
    or clinic custom holidays (`CustomHoliday`).
    """

    __tablename__ = "holiday"
    __table_args__ = (
        UniqueConstraint("user_id", "date"),
        Index("ix_holiday_clinic_id_date", "clinic_id", "date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the user for clinic-scoped filtering; must match user.clinic_id.
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OnsiteDay(UUIDPrimaryKeyMixin, Base):
    """A day the clinic requires physical presence."""

    __tablename__ = "onsite_day"
    __table_args__ = (UniqueConstraint("clinic_id", "date"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CustomHoliday(UUIDPrimaryKeyMixin, Base):
    """A clinic-specific holiday on top of the official Luxembourg ones."""

    __tablename__ = "custom_holiday"
    __table_args__ = (UniqueConstraint("clinic_id", "date"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)


class Generation(UUIDPrimaryKeyMixin, Base):
    """One run of the scheduler for a clinic. Exactly one is active per clinic."""

    __tablename__ = "generation"
    __table_args__ = (
        Index(
            "uq_generation_clinic_id_active",
            "clinic_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_generation_clinic_id_generated_at", "clinic_id", "generated_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Assignment(UUIDPrimaryKeyMixin, Base):
    """The doctor on duty for one day of a generation (NULL user = unassigned, "XX")."""

    __tablename__ = "assignment"
    __table_args__ = (UniqueConstraint("generation_id", "date"),)

    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    onsite_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssignmentDraft(UUIDPrimaryKeyMixin, Base):
    """Shared, not yet saved edit of one cell of the clinic's active generation.

    One row per edited date holds the desired final state of the cell (doctor and
    on-site flag). All rows of a clinic form its single collective draft.

    Why a table (and not a JSON field on `generation`): row-level upserts keep concurrent
    editors from overwriting each other's cells, foreign keys keep doctors valid, and the
    draft survives restarts and is visible to every user of the clinic.
    """

    __tablename__ = "assignment_draft"
    __table_args__ = (UniqueConstraint("generation_id", "date"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    onsite_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
