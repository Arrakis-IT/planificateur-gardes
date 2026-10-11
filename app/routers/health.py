"""Healthcheck endpoint used by Docker and external uptime monitoring."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db import check_database

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(db_ok: Annotated[bool, Depends(check_database)]) -> JSONResponse:
    """Return 200 if the app and database are reachable, 503 otherwise."""
    if db_ok:
        return JSONResponse({"status": "ok", "db": "ok"})
    return JSONResponse({"status": "degraded", "db": "error"}, status_code=503)
