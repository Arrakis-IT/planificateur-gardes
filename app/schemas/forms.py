# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Pydantic models for HTML form submissions (validated at the boundary).

Structural validation (types, lengths) happens here and yields a 400 page on failure.
Business rules (password policy, uniqueness…) are checked in the routes so the form can
be re-rendered with a French message.
"""

from typing import Annotated

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
    password: Password
    clinic_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] = ""
    is_superadmin: bool = False
