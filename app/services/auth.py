# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Authentication: login with account lock, server-side sessions, password reset/change.

Session design
--------------
The browser holds a signed cookie (itsdangerous) wrapping a random 256-bit token.
The database stores only the SHA-256 hash of that token in the `session` table.
A server-side table is used (rather than a self-contained signed cookie) because
sessions must be revocable: logout, password change/reset, admin password reset and
account deactivation all invalidate existing sessions immediately.
Sessions have a fixed lifetime (SESSION_MAX_AGE_DAYS) and are rotated on login.

Account lock
------------
After LOGIN_LOCK_THRESHOLD failed logins within LOGIN_LOCK_MINUTES, an
`account_locked` audit entry is written; the account refuses logins for
LOGIN_LOCK_MINUTES after that entry. Counting restarts after a lock or a success.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AuditLog, PasswordResetToken, User, UserSession
from app.repositories import users as users_repo
from app.services import audit
from app.services.emails import build_password_reset_email
from app.services.mail import MailSender
from app.services.security import (
    derive_key,
    dummy_verify,
    generate_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "pg_session"
LOGIN_ERROR_MESSAGE = "Identifiants invalides ou compte temporairement verrouillé."


def _now() -> datetime:
    return datetime.now(UTC)


# --- Session cookie ------------------------------------------------------------------


def _cookie_serializer(settings: Settings) -> URLSafeSerializer:
    key = derive_key(settings.app_secret_key.get_secret_value(), "session-cookie")
    return URLSafeSerializer(key, salt="session")


def encode_session_cookie(settings: Settings, token: str) -> str:
    """Sign the raw session token for the cookie."""
    return _cookie_serializer(settings).dumps(token)


def decode_session_cookie(settings: Settings, cookie: str) -> str | None:
    """Return the raw token if the cookie signature is valid, else None."""
    try:
        token = _cookie_serializer(settings).loads(cookie)
    except BadSignature:
        return None
    return token if isinstance(token, str) else None


async def create_session(
    db: AsyncSession, user: User, settings: Settings, request: Request | None = None
) -> str:
    """Create a session for `user` and return the signed cookie value."""
    token = generate_token()
    db.add(
        UserSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=_now() + timedelta(days=settings.session_max_age_days),
            ip_address=audit.client_ip(request),
            user_agent=audit.user_agent(request),
        )
    )
    await db.flush()
    return encode_session_cookie(settings, token)


async def resolve_session(
    db: AsyncSession, settings: Settings, cookie: str | None
) -> tuple[UserSession, User] | None:
    """Return (session, user) for a valid, unexpired session of an active user."""
    if not cookie:
        return None
    token = decode_session_cookie(settings, cookie)
    if token is None:
        return None
    stmt = (
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > _now())
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        return None
    session, user = row
    if not is_user_allowed(user):
        return None
    return session, user


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    """Delete one session (logout)."""
    await db.execute(delete(UserSession).where(UserSession.id == session_id))


async def revoke_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete every session of a user (password change, deactivation, admin reset)."""
    await db.execute(delete(UserSession).where(UserSession.user_id == user_id))


def is_user_allowed(user: User) -> bool:
    """Active user, and for clinic users an active clinic."""
    if not user.active:
        return False
    return user.is_superadmin or (user.clinic is not None and user.clinic.active)


# --- Login + account lock ------------------------------------------------------------------


@dataclass(frozen=True)
class LoginResult:
    """Outcome of a login attempt. `user` is set only on success."""

    user: User | None
    error: str | None = None


async def _last_action_time(db: AsyncSession, user_id: uuid.UUID, action: str) -> datetime | None:
    stmt = select(func.max(AuditLog.created_at)).where(
        AuditLog.user_id == user_id, AuditLog.action == action
    )
    return await db.scalar(stmt)


async def is_account_locked(db: AsyncSession, user_id: uuid.UUID, settings: Settings) -> bool:
    """True while the last `account_locked` event is younger than the lock duration."""
    locked_at = await _last_action_time(db, user_id, "account_locked")
    return locked_at is not None and locked_at > _now() - timedelta(
        minutes=settings.login_lock_minutes
    )


async def _register_failure(
    db: AsyncSession, user: User, settings: Settings, request: Request, reason: str
) -> None:
    audit.record(
        db,
        "login_failed",
        request=request,
        user_id=user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
        meta={"reason": reason},
    )
    await db.flush()
    window_start = _now() - timedelta(minutes=settings.login_lock_minutes)
    since = max(
        [window_start]
        + [
            t
            for t in [
                await _last_action_time(db, user.id, "account_locked"),
                await _last_action_time(db, user.id, "login"),
            ]
            if t is not None
        ]
    )
    failures = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.user_id == user.id,
            AuditLog.action == "login_failed",
            AuditLog.created_at > since,
        )
    )
    if (failures or 0) >= settings.login_lock_threshold:
        audit.record(
            db,
            "account_locked",
            request=request,
            user_id=user.id,
            clinic_id=user.clinic_id,
            target_type="user",
            target_id=user.id,
            meta={"failures": failures, "minutes": settings.login_lock_minutes},
        )
        logger.warning("Account locked after %s failed logins (user_id=%s)", failures, user.id)


async def authenticate(
    db: AsyncSession, email: str, password: str, settings: Settings, request: Request
) -> LoginResult:
    """Check credentials, enforce account lock, audit the attempt. Caller commits."""
    user = await users_repo.get_user_by_email(db, email)
    if user is None:
        dummy_verify(password)
        audit.record(
            db,
            "login_failed",
            request=request,
            meta={"reason": "unknown_email", "email": email.strip()[:254]},
        )
        logger.info("Login failed: unknown email")
        return LoginResult(None, LOGIN_ERROR_MESSAGE)

    if await is_account_locked(db, user.id, settings):
        dummy_verify(password)
        audit.record(
            db,
            "login_failed",
            request=request,
            user_id=user.id,
            clinic_id=user.clinic_id,
            target_type="user",
            target_id=user.id,
            meta={"reason": "locked"},
        )
        logger.info("Login refused: account locked (user_id=%s)", user.id)
        return LoginResult(None, LOGIN_ERROR_MESSAGE)

    if not verify_password(user.password_hash, password):
        await _register_failure(db, user, settings, request, "bad_password")
        logger.info("Login failed: bad password (user_id=%s)", user.id)
        return LoginResult(None, LOGIN_ERROR_MESSAGE)

    if not is_user_allowed(user):
        await _register_failure(db, user, settings, request, "inactive")
        logger.info("Login refused: inactive user or clinic (user_id=%s)", user.id)
        return LoginResult(None, LOGIN_ERROR_MESSAGE)

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _now()
    audit.record(
        db,
        "login",
        request=request,
        user_id=user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
    )
    logger.info("Login success (user_id=%s)", user.id)
    return LoginResult(user)


# --- Password change -----------------------------------------------------------------------


async def set_password(
    db: AsyncSession,
    user: User,
    new_password: str,
    request: Request | None,
    *,
    via: str,
    actor_id: uuid.UUID | None = None,
    must_change: bool = False,
) -> None:
    """Set a new password, revoke all sessions and audit. Caller commits.

    `via`: "self" (settings page), "reset" (emailed link) or "admin" (temporary password).
    """
    user.password_hash = hash_password(new_password)
    user.must_change_password = must_change
    await revoke_user_sessions(db, user.id)
    audit.record(
        db,
        "password_changed",
        request=request,
        user_id=actor_id or user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
        meta={"via": via},
    )


# --- Password reset ------------------------------------------------------------------------


async def request_password_reset(
    db: AsyncSession,
    email: str,
    settings: Settings,
    request: Request,
    mailer: MailSender,
) -> None:
    """Email a reset link if the account exists, is active and not throttled.

    Always behaves the same from the caller's point of view (no account enumeration).
    """
    user = await users_repo.get_user_by_email(db, email)
    if user is None or not is_user_allowed(user):
        logger.info("Password reset requested for unknown or inactive account")
        return

    since = _now() - timedelta(hours=1)
    recent = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.user_id == user.id,
            AuditLog.action == "password_reset_requested",
            AuditLog.created_at > since,
        )
    )
    if (recent or 0) >= settings.rate_limit_reset_per_hour:
        audit.record(
            db,
            "password_reset_throttled",
            request=request,
            user_id=user.id,
            clinic_id=user.clinic_id,
            target_type="user",
            target_id=user.id,
        )
        logger.warning("Password reset throttled (user_id=%s)", user.id)
        return

    # Only the most recent link is valid.
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=_now())
    )
    token = generate_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=_now() + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    audit.record(
        db,
        "password_reset_requested",
        request=request,
        user_id=user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
    )
    await db.flush()

    link = f"{settings.app_base_url.rstrip('/')}/reset/{token}"
    try:
        await mailer.send(
            build_password_reset_email(user, link, settings.password_reset_ttl_minutes)
        )
    except Exception:
        # The user sees the generic confirmation either way; ops see the error.
        logger.exception("Password reset email could not be sent (user_id=%s)", user.id)


async def get_valid_reset_token(
    db: AsyncSession, raw_token: str
) -> tuple[PasswordResetToken, User] | None:
    """Return (token, user) if the token exists, is unused, unexpired and the user allowed."""
    stmt = (
        select(PasswordResetToken, User)
        .join(User, User.id == PasswordResetToken.user_id)
        .where(
            PasswordResetToken.token_hash == hash_token(raw_token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > _now(),
        )
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        return None
    token, user = row
    if not is_user_allowed(user):
        return None
    return token, user


async def complete_password_reset(
    db: AsyncSession,
    token: PasswordResetToken,
    user: User,
    new_password: str,
    request: Request,
) -> None:
    """Consume the token and set the new password. Caller commits."""
    token.used_at = _now()
    await set_password(db, user, new_password, request, via="reset")
