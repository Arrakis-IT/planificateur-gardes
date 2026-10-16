# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Shared FastAPI dependencies: database session, current user, role guards.

Superadmin modes (stored server-side on the `session` row, never in the cookie):

* **Impersonation** (`session.impersonating_user_id`): the effective user becomes the
  impersonated clinic user; clinic pages behave exactly as for them. Admin pages stay
  reachable (authorised with the real user) and the banner stays visible.
* **Read-only clinic view** (`session.viewing_clinic_id`): the superadmin browses a clinic's
  pages as themselves; every non-GET request to clinic routes is refused (403).

Both are honoured only when the session's real user is a superadmin.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote, urlparse

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Clinic, User, UserSession
from app.services.auth import SESSION_COOKIE_NAME, is_user_allowed, resolve_session

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]

# Account settings page (password change). While `must_change_password` is set, only these
# paths are reachable.
ACCOUNT_SETTINGS_PATH = "/parametres"
# Users created before 0.6.0 never ticked the terms checkbox of the invitation form: until
# they accept on this page, it is the only one (besides logout) they can reach.
TERMS_ACCEPTANCE_PATH = "/acceptation-cgu"
TERMS_ALLOWED_PATHS = frozenset({TERMS_ACCEPTANCE_PATH, "/logout"})
# Gates run in this order: terms acceptance first, then the forced password change. The
# second gate must let the first gate's page through, or a user with both pending (e.g. an
# old account after an admin password reset) bounces between the two pages forever.
FORCED_CHANGE_ALLOWED_PATHS = (
    frozenset({ACCOUNT_SETTINGS_PATH, f"{ACCOUNT_SETTINGS_PATH}/mot-de-passe", "/logout"})
    | TERMS_ALLOWED_PATHS
)
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RedirectError(Exception):
    """Raised by guards to send the browser elsewhere (handled in app.main)."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


@dataclass(frozen=True)
class AuthContext:
    """The authenticated session, its real user and the effective user of the request."""

    session: UserSession
    real_user: User  # who logged in
    user: User  # effective user: the impersonated one, or real_user
    viewing_clinic: Clinic | None = None  # superadmin read-only clinic view

    @property
    def impersonating(self) -> bool:
        """A superadmin is acting as a clinic user."""
        return self.user.id != self.real_user.id

    @property
    def read_only(self) -> bool:
        """A superadmin is browsing a clinic in read-only mode."""
        return self.viewing_clinic is not None and not self.impersonating

    @property
    def is_superadmin(self) -> bool:
        """Authorisation for admin pages always uses the real user."""
        return self.real_user.is_superadmin

    @property
    def clinic(self) -> Clinic | None:
        """Clinic shown to this request (own, impersonated user's, or viewed read-only)."""
        if self.impersonating or not self.real_user.is_superadmin:
            return self.user.clinic
        return self.viewing_clinic


async def load_auth_context(
    db: AsyncSession, settings: Settings, cookie: str | None
) -> AuthContext | None:
    """Resolve a session cookie into an AuthContext (None when anonymous or invalid)."""
    resolved = await resolve_session(db, settings, cookie)
    if resolved is None:
        return None
    session, user = resolved
    if not user.is_superadmin:
        # Mode columns are ignored for clinic users, whatever their value.
        return AuthContext(session, user, user)
    if session.impersonating_user_id:
        target = await db.get(User, session.impersonating_user_id)
        if target is not None and not target.is_superadmin and is_user_allowed(target):
            return AuthContext(session, user, target)
    viewing = await db.get(Clinic, session.viewing_clinic_id) if session.viewing_clinic_id else None
    return AuthContext(session, user, user, viewing)


async def get_auth(request: Request, db: DbSession, settings: AppSettings) -> AuthContext | None:
    """Resolve the session cookie (once per request); None when anonymous."""
    if hasattr(request.state, "auth"):
        cached: AuthContext | None = request.state.auth
        return cached
    auth = await load_auth_context(db, settings, request.cookies.get(SESSION_COOKIE_NAME))
    request.state.auth = auth
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_auth)]


def safe_next_url(value: str | None, default: str = "/") -> str:
    """A redirect target from the request, if it is a local absolute path; else `default`.

    Refused (open redirect): empty, not starting with "/", protocol-relative ("//host"),
    containing "://" or a backslash, control characters (browsers strip tabs / newlines:
    "/\t/evil.example" would become "//evil.example"), or anything urlparse sees a scheme or
    a host in.
    """
    if not value:
        return default
    parsed = urlparse(value)
    if (
        not parsed.scheme
        and not parsed.netloc
        and value.startswith("/")
        and not value.startswith("//")
        and "://" not in value
        and "\\" not in value
        and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    ):
        return value
    return default


def require_user(request: Request, auth: OptionalAuth) -> AuthContext:
    """Authenticated user; anonymous → login page, terms not accepted yet → acceptance page,
    forced password change → settings."""
    if auth is None:
        target = request.url.path
        if request.url.query:
            target += f"?{request.url.query}"
        raise RedirectError(f"/login?next={quote(target)}")
    if auth.real_user.accepted_terms_at is None and request.url.path not in TERMS_ALLOWED_PATHS:
        raise RedirectError(TERMS_ACCEPTANCE_PATH)
    if auth.real_user.must_change_password and request.url.path not in FORCED_CHANGE_ALLOWED_PATHS:
        raise RedirectError(ACCOUNT_SETTINGS_PATH)
    return auth


CurrentAuth = Annotated[AuthContext, Depends(require_user)]


def require_superadmin(auth: CurrentAuth) -> AuthContext:
    """Superadmin only (real user, so admin pages stay reachable while impersonating)."""
    if not auth.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès réservé à l'administrateur.")
    return auth


SuperadminAuth = Annotated[AuthContext, Depends(require_superadmin)]


def require_clinic_user(request: Request, auth: CurrentAuth) -> AuthContext:
    """Clinic context: clinic user, impersonating superadmin, or read-only view (GET only)."""
    if auth.read_only:
        if request.method not in READ_METHODS:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Mode lecture : pour modifier, connectez-vous en tant qu'un médecin du groupe.",
            )
        return auth
    if auth.user.is_superadmin:
        raise RedirectError("/admin")
    return auth


ClinicAuth = Annotated[AuthContext, Depends(require_clinic_user)]


def clinic_scope(auth: ClinicAuth) -> uuid.UUID:
    """The clinic every scoped query of this request must filter on."""
    clinic = auth.clinic
    if clinic is None:  # pragma: no cover - excluded by require_clinic_user
        raise RedirectError("/admin")
    return clinic.id


ClinicScope = Annotated[uuid.UUID, Depends(clinic_scope)]
