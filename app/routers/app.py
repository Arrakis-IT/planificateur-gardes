# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""User-facing clinic app. Phase 2: placeholder home listing the clinic's doctors."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.dependencies import ClinicAuth, ClinicScope, DbSession
from app.repositories import users as users_repo
from app.templating import render

router = APIRouter(tags=["app"])


@router.get("/")
async def home(
    request: Request, auth: ClinicAuth, clinic_id: ClinicScope, db: DbSession
) -> Response:
    """Home page (the today/tomorrow dashboard arrives in phase 4)."""
    doctors = await users_repo.list_clinic_users(db, clinic_id)
    return render(request, "app/home.html", {"doctors": doctors, "clinic": auth.user.clinic})
