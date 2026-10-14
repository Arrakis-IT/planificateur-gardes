# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Notification email preference and its one-click unsubscribe link.

Notification emails (schedule generated / edited, and future notifications) respect
`user.email_notifications_enabled`; invitations and password resets are always sent.

The unsubscribe link needs no login: it carries the user id signed with a key derived from
APP_SECRET_KEY (itsdangerous URLSafeTimedSerializer: HMAC signature + timestamp, the same
mechanism as the session cookie and flash messages). A modified or older-than-a-year link
is rejected.
"""

import uuid

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import User
from app.services import audit
from app.services.security import derive_key

UNSUBSCRIBE_TTL_SECONDS = 365 * 24 * 3600
UNSUBSCRIBE_PATH = "/desabonnement"


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    key = derive_key(settings.app_secret_key.get_secret_value(), "unsubscribe")
    return URLSafeTimedSerializer(key, salt="unsubscribe")


def unsubscribe_url(settings: Settings, user_id: uuid.UUID) -> str:
    """Absolute one-click link for a notification email."""
    token = _serializer(settings).dumps(str(user_id))
    return f"{settings.app_base_url.rstrip('/')}{UNSUBSCRIBE_PATH}/{token}"


def read_unsubscribe_token(
    settings: Settings, token: str, max_age: int = UNSUBSCRIBE_TTL_SECONDS
) -> uuid.UUID | None:
    """User id of a valid, unexpired link; None if modified, expired or malformed."""
    try:
        value = _serializer(settings).loads(token, max_age=max_age)
        return uuid.UUID(value)
    except (BadSignature, ValueError, TypeError):
        return None


async def set_email_notifications(
    db: AsyncSession,
    user: User,
    enabled: bool,
    *,
    source: str,
    request: Request | None = None,
) -> bool:
    """Set the preference; audited when it changes (`source`: "settings" or
    "one_click_link"). Returns False when unchanged. Caller commits."""
    if user.email_notifications_enabled == enabled:
        return False
    user.email_notifications_enabled = enabled
    audit.record(
        db,
        "email_notifications_toggled",
        request=request,
        user_id=user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
        meta={"enabled": enabled, "source": source},
    )
    return True
