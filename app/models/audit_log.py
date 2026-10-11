# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Append-only audit trail of security-relevant and business actions."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """One audited action. No foreign keys: entries must survive deletion of their subjects."""

    __tablename__ = "audit_log"
    __table_args__ = (
        # Account lock / reset throttling look up recent actions per user.
        Index("ix_audit_log_user_id_action_created_at", "user_id", "action", "created_at"),
        Index("ix_audit_log_clinic_id_created_at", "clinic_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    clinic_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    # "metadata" is reserved by SQLAlchemy's declarative API; the column keeps the brief's name.
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
