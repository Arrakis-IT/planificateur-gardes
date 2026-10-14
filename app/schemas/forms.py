# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Pydantic models for HTML form submissions (validated at the boundary).

Structural validation (types, lengths) happens here and yields a 400 page on failure.
Business rules (password policy, uniqueness…) are checked in the routes so the form can
be re-rendered with a French message.
"""

from datetime import date as date_type
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.services.turnstile import FORM_FIELD as TURNSTILE_FIELD

# Passwords are never stripped; the upper bound only prevents hashing huge inputs.
Password = Annotated[str, StringConstraints(max_length=1024)]
EmailInput = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


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


class ChangePasswordForm(NewPasswordForm):
    """POST /parametres/mot-de-passe."""

    current_password: Password


class ClinicForm(_Form):
    """Create / rename a clinic."""

    name: Name


class UserCreateForm(_Form):
    """POST /admin/users."""

    email: EmailStr
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)
    ]
    clinic_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] = ""
    is_superadmin: bool = False


SemesterKey = Annotated[str, StringConstraints(max_length=7)]


class DayForm(_Form):
    """Toggle one day (vacation or on-site)."""

    date: date_type
    semestre: SemesterKey | None = None


class RangeForm(_Form):
    """Add / remove a range of vacation days."""

    start: date_type
    end: date_type
    action: Literal["add", "remove"] = "add"
    semestre: SemesterKey | None = None


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
