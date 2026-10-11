# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Shared FastAPI dependencies: database session, current user, role guards."""

import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import User, UserSession
from app.repositories.scope import get_clinic_scope
from app.services.auth import SESSION_COOKIE_NAME, resolve_session

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]

PASSWORD_CHANGE_PATH = "/parametres"  # noqa: S105 - URL path, not a secret
# Reachable while `must_change_password` is set.
PASSWORD_CHANGE_ALLOWED_PATHS = frozenset(
    {PASSWORD_CHANGE_PATH, "/parametres/mot-de-passe", "/logout"}
)


class RedirectError(Exception):
    """Raised by guards to send the browser elsewhere (handled in app.main)."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


@dataclass(frozen=True)
class AuthContext:
    """The authenticated session and its user."""

    session: UserSession
    user: User


async def get_auth(request: Request, db: DbSession, settings: AppSettings) -> AuthContext | None:
    """Resolve the session cookie (once per request); None when anonymous."""
    if hasattr(request.state, "auth"):
        cached: AuthContext | None = request.state.auth
        return cached
    resolved = await resolve_session(db, settings, request.cookies.get(SESSION_COOKIE_NAME))
    auth = AuthContext(*resolved) if resolved else None
    request.state.auth = auth
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_auth)]


def safe_next_path(value: str | None) -> str:
    """Only allow local absolute paths as post-login redirect targets (no open redirect)."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        # Browsers strip tabs/newlines: "/\t/evil.example" would become "//evil.example".
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    ):
        return "/"
    return value


async def require_user(request: Request, auth: OptionalAuth) -> AuthContext:
    """Authenticated user; anonymous → login page, forced password change → settings."""
    if auth is None:
        target = request.url.path
        if request.url.query:
            target += f"?{request.url.query}"
        raise RedirectError(f"/login?next={quote(target)}")
    if auth.user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
        raise RedirectError(PASSWORD_CHANGE_PATH)
    return auth


CurrentAuth = Annotated[AuthContext, Depends(require_user)]


async def require_superadmin(auth: CurrentAuth) -> AuthContext:
    """Superadmin only; 403 for clinic users."""
    if not auth.user.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès réservé à l'administrateur.")
    return auth


SuperadminAuth = Annotated[AuthContext, Depends(require_superadmin)]


async def require_clinic_user(auth: CurrentAuth) -> AuthContext:
    """Clinic user only; superadmins (not impersonating) are sent to the admin panel."""
    if auth.user.is_superadmin:
        raise RedirectError("/admin")
    return auth


ClinicAuth = Annotated[AuthContext, Depends(require_clinic_user)]


async def clinic_scope(auth: ClinicAuth) -> uuid.UUID:
    """The clinic every scoped query of this request must filter on."""
    return get_clinic_scope(auth.user)


ClinicScope = Annotated[uuid.UUID, Depends(clinic_scope)]
