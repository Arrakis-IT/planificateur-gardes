# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Public iCal feed of a doctor's duties (subscription URL, see app/services/ical.py)."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from app.dependencies import DbSession
from app.middleware.rate_limit import ICAL_LIMIT, limiter
from app.services.ical import ICAL_PATH, calendar_for, user_by_token

router = APIRouter(tags=["ical"])

UNKNOWN_FEED = "Calendrier introuvable."


@router.get(f"{ICAL_PATH}/{{token}}", responses={404: {"description": UNKNOWN_FEED}})
@limiter.limit(ICAL_LIMIT)
async def ical_feed(request: Request, token: str, db: DbSession) -> Response:
    """VCALENDAR of the token owner's duties; empty (not 404) without an active generation,
    so that subscriptions stay alive between generations."""
    user = await user_by_token(db, token)
    if user is None:
        raise HTTPException(404, UNKNOWN_FEED)
    return Response(
        await calendar_for(db, user),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )
