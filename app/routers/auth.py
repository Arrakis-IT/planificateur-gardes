# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Login, logout, password reset and invitation routes (anonymous-facing)."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.config import Settings
from app.dependencies import (
    ACCOUNT_SETTINGS_PATH,
    AppSettings,
    DbSession,
    OptionalAuth,
    safe_next_path,
)
from app.middleware.rate_limit import limiter, login_limit, reset_limit
from app.schemas.forms import InvitationForm, LoginForm, NewPasswordForm, ResetRequestForm
from app.services import audit
from app.services.auth import (
    SESSION_COOKIE_NAME,
    authenticate,
    complete_password_reset,
    create_session,
    get_valid_reset_token,
    request_password_reset,
    revoke_session,
)
from app.services.mail import MailSender, get_mail_sender
from app.services.security import validate_new_password
from app.services.turnstile import TurnstileVerifier, get_turnstile_verifier
from app.templating import render, set_flash

router = APIRouter(tags=["auth"])

Turnstile = Annotated[TurnstileVerifier, Depends(get_turnstile_verifier)]
Mailer = Annotated[MailSender, Depends(get_mail_sender)]

LOGIN_TEMPLATE = "auth/login.html"
TURNSTILE_ERROR = "La vérification anti-robot a échoué. Veuillez réessayer."


def set_session_cookie(response: Response, cookie_value: str, settings: Settings) -> None:
    """Attach the signed session cookie (HttpOnly, Secure in production)."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_value,
        max_age=settings.session_max_age_days * 86400,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


@router.get("/login")
async def login_page(request: Request, auth: OptionalAuth, next: str = "/") -> Response:
    """Login form; already authenticated users go straight to their target."""
    if auth is not None:
        return RedirectResponse(safe_next_path(next), status_code=303)
    return render(request, LOGIN_TEMPLATE, {"next": safe_next_path(next)})


@router.post("/login")
@limiter.limit(login_limit)
async def login(
    request: Request,
    form: Annotated[LoginForm, Form()],
    db: DbSession,
    settings: AppSettings,
    turnstile: Turnstile,
    auth: OptionalAuth,
) -> Response:
    """Authenticate, rotate the session and redirect."""
    next_path = safe_next_path(form.next)
    context = {"next": next_path, "email": form.email}
    if not await turnstile.verify(form.turnstile, audit.client_ip(request)):
        return render(request, LOGIN_TEMPLATE, context | {"error": TURNSTILE_ERROR}, 400)

    result = await authenticate(db, form.email, form.password, settings, request)
    if result.user is None:
        await db.commit()  # persist the audit trail of the failure
        return render(request, LOGIN_TEMPLATE, context | {"error": result.error}, 401)

    # Session rotation: drop any session the browser already had.
    if auth is not None:
        await revoke_session(db, auth.session.id)
    cookie = await create_session(db, result.user, settings, request)
    await db.commit()

    target = ACCOUNT_SETTINGS_PATH if result.user.must_change_password else next_path
    if result.user.is_superadmin and target == "/":
        target = "/admin"
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, cookie, settings)
    return response


@router.post("/logout")
async def logout(request: Request, db: DbSession, auth: OptionalAuth) -> Response:
    """Revoke the current session and clear the cookie."""
    if auth is not None:
        await revoke_session(db, auth.session.id)
        audit.record(
            db,
            "logout",
            request=request,
            user_id=auth.real_user.id,
            clinic_id=auth.real_user.clinic_id,
            target_type="user",
            target_id=auth.real_user.id,
        )
        await db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/reset")
async def reset_request_page(request: Request) -> Response:
    """Form asking for the account email."""
    return render(request, "auth/reset_request.html")


@router.post("/reset")
@limiter.limit(reset_limit)
async def reset_request(
    request: Request,
    form: Annotated[ResetRequestForm, Form()],
    db: DbSession,
    settings: AppSettings,
    turnstile: Turnstile,
    mailer: Mailer,
) -> Response:
    """Send a reset link if possible; the response never reveals whether the account exists."""
    if not await turnstile.verify(form.turnstile, audit.client_ip(request)):
        return render(
            request, "auth/reset_request.html", {"error": TURNSTILE_ERROR, "email": form.email}, 400
        )
    await request_password_reset(db, form.email, settings, request, mailer)
    await db.commit()
    return render(request, "auth/reset_sent.html")


# --- Emailed password links -----------------------------------------------------------------
# /reset/{token} (forgotten password) and /invitation/{token} (first password of a new user)
# share the single-use token table, the form and the checks; only the wording differs.

PasswordLink = Literal["reset", "invitation"]
DONE_MESSAGES: dict[PasswordLink, str] = {
    "reset": "Votre mot de passe a été modifié. Vous pouvez vous connecter.",
    "invitation": "Votre mot de passe est enregistré. Vous pouvez vous connecter.",
}
TERMS_REQUIRED = (
    "Vous devez accepter les conditions générales d'utilisation et la politique de confidentialité."
)


async def _password_link_form(
    request: Request, db: DbSession, kind: PasswordLink, token: str
) -> Response:
    context = {"kind": kind, "token": token}
    if await get_valid_reset_token(db, token) is None:
        return render(request, "auth/reset_invalid.html", context, 400)
    return render(request, "auth/reset_form.html", context)


async def _password_link_submit(
    request: Request, db: DbSession, kind: PasswordLink, token: str, form: NewPasswordForm
) -> Response:
    context = {"kind": kind, "token": token}
    found = await get_valid_reset_token(db, token)
    if found is None:
        return render(request, "auth/reset_invalid.html", context, 400)
    reset_token, user = found
    accepts_terms = isinstance(form, InvitationForm) and form.accept_terms
    if kind == "invitation" and not accepts_terms:
        error: str | None = TERMS_REQUIRED
    else:
        error = validate_new_password(form.password, form.password_confirm, user.email)
    if error:
        return render(request, "auth/reset_form.html", context | {"error": error}, 400)
    await complete_password_reset(db, reset_token, user, form.password, request, via=kind)
    if accepts_terms:
        user.accepted_terms_at = datetime.now(UTC)
        audit.record(
            db,
            "terms_accepted",
            request=request,
            user_id=user.id,
            clinic_id=user.clinic_id,
            target_type="user",
            target_id=user.id,
        )
    await db.commit()
    response = RedirectResponse("/login", status_code=303)
    set_flash(response, DONE_MESSAGES[kind])
    return response


@router.get("/reset/{token}")
async def reset_form(request: Request, token: str, db: DbSession) -> Response:
    """New password form, or an 'invalid link' page."""
    return await _password_link_form(request, db, "reset", token)


@router.post("/reset/{token}")
async def reset_submit(
    request: Request, token: str, form: Annotated[NewPasswordForm, Form()], db: DbSession
) -> Response:
    """Consume the token and set the new password."""
    return await _password_link_submit(request, db, "reset", token, form)


@router.get("/invitation/{token}")
async def invitation_form(request: Request, token: str, db: DbSession) -> Response:
    """First password form of an invited user, or an 'invalid link' page."""
    return await _password_link_form(request, db, "invitation", token)


@router.post("/invitation/{token}")
async def invitation_submit(
    request: Request, token: str, form: Annotated[InvitationForm, Form()], db: DbSession
) -> Response:
    """Consume the invitation token and set the first password (terms must be accepted)."""
    return await _password_link_submit(request, db, "invitation", token, form)
