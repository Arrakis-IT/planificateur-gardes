# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
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
from app.repositories.scope import NoClinicScopeError
from app.routers import account, admin, auth, health
from app.routers import app as app_router
from app.services.bootstrap import ensure_initial_superadmin
from app.templating import render
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


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
    yield
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

    app = FastAPI(
        title="Planificateur de Gardes",
        version=settings.app_release,
        lifespan=lifespan,
        # No public API docs in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        dependencies=[Depends(verify_csrf)],
    )
    app.state.limiter = limiter
    app.add_middleware(CSRFCookieMiddleware)
    _register_error_handlers(app)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(app_router.router)
    return app


app = create_app()
