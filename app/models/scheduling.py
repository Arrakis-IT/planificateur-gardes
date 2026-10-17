# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Scheduling data: doctor vacations and desiderata, on-site days, custom holidays,
generations."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
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

USER_FK = "user.id"
CLINIC_FK = "clinic.id"
GENERATION_FK = "generation.id"
CASCADE = "CASCADE"
SET_NULL = "SET NULL"


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
        ForeignKey(USER_FK, ondelete=CASCADE), nullable=False
    )
    # Denormalised from the user for clinic-scoped filtering; must match user.clinic_id.
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DesiderataDay(UUIDPrimaryKeyMixin, Base):
    """A day a doctor would rather not be on duty (soft constraint, unlike a vacation).

    The scheduler avoids the doctor that day unless every available doctor has a desiderata
    (see app/services/scheduler.py).
    """

    __tablename__ = "desiderata_day"
    __table_args__ = (
        UniqueConstraint("user_id", "date"),
        Index("ix_desiderata_day_clinic_id_date", "clinic_id", "date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(USER_FK, ondelete=CASCADE), nullable=False
    )
    # Denormalised from the user for clinic-scoped filtering; must match user.clinic_id.
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DesiderataConflict(enum.StrEnum):
    """Why the doctor of an assignment had a desiderata that day (`desiderata_conflict`)."""

    GENERATED = "generated"  # the scheduler had no alternative
    MANUAL = "manual"  # a manual edit put the doctor on the day


class OnsiteDay(UUIDPrimaryKeyMixin, Base):
    """A day the clinic requires physical presence."""

    __tablename__ = "onsite_day"
    __table_args__ = (UniqueConstraint("clinic_id", "date"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CustomHoliday(UUIDPrimaryKeyMixin, Base):
    """A clinic-specific holiday on top of the official Luxembourg ones."""

    __tablename__ = "custom_holiday"
    __table_args__ = (UniqueConstraint("clinic_id", "date"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)


class MonthLock(UUIDPrimaryKeyMixin, Base):
    """A month of a clinic frozen by its group admin (no edits of assignments, vacations or
    on-site days falling in it). `month` is the first day of the month."""

    __tablename__ = "month_lock"
    __table_args__ = (UniqueConstraint("clinic_id", "month"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False
    )
    month: Mapped[date] = mapped_column(Date, nullable=False)
    locked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False
    )
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    generated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Assignment(UUIDPrimaryKeyMixin, Base):
    """The doctor on duty for one day of a generation (NULL user = unassigned, "XX")."""

    __tablename__ = "assignment"
    __table_args__ = (UniqueConstraint("generation_id", "date"),)

    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(GENERATION_FK, ondelete=CASCADE), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    onsite_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # Effective on-site status as last published (generation or saved edits, i.e. what the
    # emailed PDF showed). On-site settings can change afterwards, so the changelog of the
    # next generation compares against this snapshot. NULL: generated before 0.4.0.
    onsite: Mapped[bool | None] = mapped_column(Boolean)
    # Set when the doctor had a desiderata that day, as of the generation or the saved edit
    # (later changes of the desiderata do not update it). NULL: no conflict.
    desiderata_conflict: Mapped[DesiderataConflict | None] = mapped_column(
        Enum(
            DesiderataConflict,
            name="desiderata_conflict",
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def desiderata_violation(self) -> bool:
        """The doctor on duty had asked not to be that day."""
        return self.desiderata_conflict is not None


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
        ForeignKey(CLINIC_FK, ondelete=CASCADE), nullable=False, index=True
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(GENERATION_FK, ondelete=CASCADE), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    onsite_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK, ondelete=SET_NULL))
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
