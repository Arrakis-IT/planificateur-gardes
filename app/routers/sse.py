# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Live channel per clinic: presence heartbeat and Server-Sent Events stream.

Reverse proxy note: the stream needs `proxy_buffering off` and a read timeout longer than
the keep-alive ping (SSE_PING_SECONDS); the response also sends `X-Accel-Buffering: no`.
"""

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from app.config import get_settings
from app.db import get_sessionmaker
from app.dependencies import ClinicAuth, ClinicScope, load_auth_context
from app.models import User
from app.services.auth import SESSION_COOKIE_NAME
from app.services.presence import PresentUser, hub

router = APIRouter(prefix="/sse", tags=["sse"])

SSE_PING_SECONDS = 15


def present_user(user: User) -> PresentUser:
    """Public presence info of a user."""
    return PresentUser(user.id, user.doctor_code, user.full_name, user.color)


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(auth: ClinicAuth, clinic_id: ClinicScope) -> Response:
    """Called every 10 s by open pages: keeps the user "present" for 30 s."""
    hub.heartbeat(clinic_id, auth.session.id, present_user(auth.user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/presence")
async def presence_stream(request: Request) -> Response:
    """Event stream of the user's clinic (user_entered, user_left, draft_changed, draft_cleared).

    Authentication uses a short-lived DB session: a stream stays open for hours and must
    not hold a pooled connection.
    """
    async with get_sessionmaker()() as db:
        auth = await load_auth_context(db, get_settings(), request.cookies.get(SESSION_COOKIE_NAME))
    if auth is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    clinic = auth.clinic
    if clinic is None or auth.read_only or auth.real_user.must_change_password:
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    user = auth.user

    hub.heartbeat(clinic.id, auth.session.id, present_user(user))
    connection = hub.connect(clinic.id, present_user(user))
    return EventSourceResponse(
        hub.stream(connection),
        ping=SSE_PING_SECONDS,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        # Also unregister if the client leaves before the generator ever started.
        background=BackgroundTask(hub.disconnect, connection),
    )
