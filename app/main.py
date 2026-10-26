# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.dependencies import RedirectError
from app.middleware.csrf import CSRFCookieMiddleware, verify_csrf
from app.middleware.rate_limit import limiter
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.observability import app_version, init_sentry
from app.repositories.scope import NoClinicScopeError
from app.routers import (
    account,
    admin,
    admin_ops,
    auth,
    editing,
    generation,
    health,
    holidays,
    ical,
    legal,
    pwa,
    sse,
)
from app.routers import app as app_router
from app.services import calendar as cal
from app.services.bootstrap import ensure_initial_superadmin
from app.services.locks import MonthLockedError
from app.services.planning import archive_expired
from app.services.presence import hub
from app.templating import render
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


async def archive_expired_generations() -> int:
    """Archive the active generations whose whole period is past (all clinics). Returns
    how many were archived."""
    async with get_sessionmaker()() as db:
        archived = await archive_expired(db, cal.today_local())
        await db.commit()
    if archived:
        logger.info("Archived %s expired generation(s)", len(archived))
    return len(archived)


def _seconds_until_next_run(now: datetime | None = None) -> float:
    """Seconds until 00:05 Luxembourg time (just after the day changes)."""
    now = now or datetime.now(cal.CLINIC_TZ)
    local = now.astimezone(cal.CLINIC_TZ)
    target = local.replace(hour=0, minute=5, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return (target - local).total_seconds()


async def _archive_expired_daily() -> None:
    """At startup, then every night: a generation expires when its last day is past."""
    while True:
        try:
            await archive_expired_generations()
        except Exception:
            logger.exception("Archiving expired generations failed")
        await asyncio.sleep(_seconds_until_next_run())


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""
    settings = get_settings()
    logger.info("Application starting (env=%s)", settings.app_env)
    try:
        await ensure_initial_superadmin(get_sessionmaker(), settings)
    except Exception:
        # Never prevent startup (healthcheck will report DB problems).
        logger.exception("Superadmin bootstrap failed")
    hub.start()
    archiver = asyncio.create_task(_archive_expired_daily())
    yield
    archiver.cancel()
    await hub.stop()
    await dispose_engine()
    logger.info("Application stopped")


def _error_page(request: Request, status_code: int, message: str) -> Response:
    return render(
        request, "errors/error.html", {"status_code": status_code, "message": message}, status_code
    )


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RedirectError)
    async def handle_redirect(_request: Request, exc: RedirectError) -> Response:
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(NoClinicScopeError)
    async def handle_no_scope(_request: Request, _exc: NoClinicScopeError) -> Response:
        return RedirectResponse("/admin", status_code=303)

    @app.exception_handler(MonthLockedError)
    async def handle_month_locked(request: Request, exc: MonthLockedError) -> Response:
        return _error_page(request, 403, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> Response:
        messages = {404: "Page introuvable.", 405: "Méthode non autorisée."}
        detail = exc.detail if isinstance(exc.detail, str) else ""
        if exc.status_code in messages and detail in {"Not Found", "Method Not Allowed"}:
            detail = messages[exc.status_code]
        return _error_page(request, exc.status_code, detail or "Erreur.")

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, _exc: RequestValidationError) -> Response:
        return _error_page(request, 400, "Données envoyées invalides.")

    @app.exception_handler(RateLimitExceeded)
    async def handle_rate_limit(request: Request, _exc: RateLimitExceeded) -> Response:
        logger.warning("Rate limit exceeded on %s", request.url.path)
        return _error_page(
            request, 429, "Trop de tentatives. Veuillez patienter avant de réessayer."
        )


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)
    init_sentry(settings)

    app = FastAPI(
        title="Planificateur de Gardes",
        version=app_version(),
        lifespan=lifespan,
        # No public API docs in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        dependencies=[Depends(verify_csrf)],
    )
    app.state.limiter = limiter
    app.add_middleware(CSRFCookieMiddleware)
    # Added last = outermost: headers also land on error pages and redirects.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    _register_error_handlers(app)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(pwa.router)
    app.include_router(legal.router)
    app.include_router(ical.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(admin_ops.router)
    app.include_router(holidays.router)
    app.include_router(editing.router)
    app.include_router(generation.router)
    app.include_router(sse.router)
    app.include_router(app_router.router)
    if settings.app_env == "development":
        app.include_router(dev_tools_router())
    return app


def dev_tools_router() -> APIRouter:
    """DEVELOPMENT ONLY: routes to check integrations by hand (never mounted elsewhere)."""
    router = APIRouter(prefix="/_dev", include_in_schema=False)

    @router.get("/sentry-test")
    async def sentry_test() -> None:
        """Raise an unhandled error to check that it reaches Sentry."""
        raise ValueError("Sentry test")

    return router


app = create_app()
