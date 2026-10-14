# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Builders for the transactional emails (French content, Jinja templates)."""

from app.models import User
from app.services.mail import OutgoingEmail
from app.templating import render_email


def build_password_reset_email(user: User, link: str, ttl_minutes: int) -> OutgoingEmail:
    """Password reset email containing the single-use link."""
    context = {"full_name": user.full_name, "link": link, "ttl_minutes": ttl_minutes}
    return OutgoingEmail(
        to=user.email,
        subject="Réinitialisation de votre mot de passe — Planificateur des gardes",
        text=render_email("password_reset.txt", context),
        html=render_email("password_reset.html", context),
    )


def build_invitation_email(user: User, link: str, ttl_hours: int) -> OutgoingEmail:
    """Welcome email of a new user, with the single-use link to choose a first password."""
    context = {"full_name": user.full_name, "link": link, "ttl_hours": ttl_hours}
    return OutgoingEmail(
        to=user.email,
        subject="Bienvenue sur le Planificateur des gardes",
        text=render_email("invitation.txt", context),
        html=render_email("invitation.html", context),
    )
