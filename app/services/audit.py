# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Write entries to the audit log."""

import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

USER_AGENT_MAX_LENGTH = 512


def client_ip(request: Request | None) -> str | None:
    """Client IP as seen by the app (uvicorn --proxy-headers resolves X-Forwarded-For)."""
    if request is None or request.client is None:
        return None
    host = request.client.host
    # The in-process test client reports a non-IP host ("testclient"); INET would reject it.
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def user_agent(request: Request | None) -> str | None:
    """Truncated User-Agent header."""
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:USER_AGENT_MAX_LENGTH] if ua else None


def record(
    db: AsyncSession,
    action: str,
    *,
    request: Request | None = None,
    user_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit entry to the current transaction (the caller commits).

    Never put passwords, tokens or other secrets in `meta`.
    """
    auth = getattr(request.state, "auth", None) if request is not None else None
    if auth is not None and auth.impersonating:
        # Actions done "as" a clinic user stay attributable to the superadmin behind them.
        meta = {**(meta or {}), "impersonated_by": str(auth.real_user.id)}
    entry = AuditLog(
        action=action,
        user_id=user_id,
        clinic_id=clinic_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        meta=meta,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
    db.add(entry)
    return entry
