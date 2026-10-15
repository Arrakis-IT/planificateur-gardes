# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Pydantic models for HTML form submissions (validated at the boundary).

Structural validation (types, lengths) happens here and yields a 400 page on failure.
Business rules (password policy, uniqueness…) are checked in the routes so the form can
be re-rendered with a French message.
"""

import uuid
from datetime import date as date_type
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.services.turnstile import FORM_FIELD as TURNSTILE_FIELD

# Passwords are never stripped; the upper bound only prevents hashing huge inputs.
Password = Annotated[str, StringConstraints(max_length=1024)]
EmailInput = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class _Form(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LoginForm(_Form):
    """POST /login."""

    email: EmailInput
    password: Password
    next: Annotated[str, StringConstraints(max_length=2048)] = "/"
    turnstile: str | None = Field(default=None, alias=TURNSTILE_FIELD, max_length=4096)


class ResetRequestForm(_Form):
    """POST /reset."""

    email: EmailInput
    turnstile: str | None = Field(default=None, alias=TURNSTILE_FIELD, max_length=4096)


class NewPasswordForm(_Form):
    """POST /reset/{token}."""

    password: Password
    password_confirm: Password


class NotificationsForm(_Form):
    """POST /parametres/notifications (checkbox: absent means off)."""

    enabled: bool = False


class TermsForm(_Form):
    """POST /acceptation-cgu (users created before the terms checkbox existed)."""

    accept_terms: bool = False


class InvitationForm(NewPasswordForm):
    """POST /invitation/{token}: first password + mandatory acceptance of the terms."""

    accept_terms: bool = False


class ChangePasswordForm(NewPasswordForm):
    """POST /parametres/mot-de-passe."""

    current_password: Password


class ClinicForm(_Form):
    """Create / rename a clinic."""

    name: Name


class UserCreateForm(_Form):
    """POST /admin/users."""

    email: EmailStr
    full_name: FullName
    clinic_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] = ""
    is_superadmin: bool = False


class ProfileForm(_Form):
    """POST /parametres/profil and /admin/users/{id}/rename."""

    full_name: FullName


MonthKey = Annotated[str, StringConstraints(max_length=7)]


class MonthLockForm(_Form):
    """Lock / unlock one month (settings or admin clinic sheet)."""

    month: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]
    action: Literal["lock", "unlock"]


class PaletteForm(_Form):
    """Change the clinic's doctor color palette (confirmation mandatory)."""

    palette: Annotated[str, StringConstraints(max_length=20)]
    confirm: bool = False


HexColor = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^#[0-9A-Fa-f]{6}$")]


class DoctorColorForm(_Form):
    """Manual color of one doctor (must belong to the clinic's active palette)."""

    color: HexColor
    user_id: uuid.UUID | None = None  # settings page; the admin route has it in the path


class GroupAdminForm(_Form):
    """POST /admin/clinic/{id}/group-admin."""

    user_id: uuid.UUID


class DayForm(_Form):
    """Toggle one day (vacation or on-site)."""

    date: date_type
    debut: MonthKey | None = None


class RangeForm(_Form):
    """Add / remove a range of vacation days."""

    start: date_type
    end: date_type
    action: Literal["add", "remove"] = "add"
    debut: MonthKey | None = None


class GenerateForm(_Form):
    """POST /generer."""

    period_from: date_type
    period_to: date_type
    confirm_overwrite: bool = False


ReturnTo = Annotated[str, StringConstraints(max_length=2048)]


class ReturnToForm(_Form):
    """Forms that send the user back to the editable view they came from."""

    retour: ReturnTo | None = None


class CellDoctorForm(ReturnToForm):
    """POST /resultats/jour/{day}: doctor code or "XX"."""

    doctor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=3)]


class CustomHolidayForm(_Form):
    """POST /admin/clinic/{id}/holidays."""

    date: date_type
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ReauthForm(_Form):
    """Destructive admin actions ask the superadmin's password again."""

    password: Password = ""
