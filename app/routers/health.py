# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Health endpoints.

- /health: liveness for external uptime monitoring (UptimeRobot): no database, no session,
  no rate limit, not written to the access log (see app/utils/logging.py).
- /healthz: readiness with a database round-trip (Docker healthcheck).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db import check_database

router = APIRouter(tags=["health"])

LIVENESS_PATH = "/health"


@router.api_route(LIVENESS_PATH, methods=["GET", "HEAD"])
async def health() -> JSONResponse:
    """The app answers (HEAD too: uptime monitors often use it)."""
    return JSONResponse({"status": "ok"})


@router.get("/healthz")
async def healthz(db_ok: Annotated[bool, Depends(check_database)]) -> JSONResponse:
    """Return 200 if the app and database are reachable, 503 otherwise."""
    if db_ok:
        return JSONResponse({"status": "ok", "db": "ok"})
    return JSONResponse({"status": "degraded", "db": "error"}, status_code=503)
