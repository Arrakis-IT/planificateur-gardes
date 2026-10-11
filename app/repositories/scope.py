# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Effective clinic scope of the current request."""

import uuid

from app.models import User


class NoClinicScopeError(Exception):
    """The current user has no clinic (superadmin not impersonating anyone)."""


def get_clinic_scope(user: User) -> uuid.UUID:
    """Return the clinic every scoped query of this request must filter on.

    Clinic users: their own clinic. Superadmins have none until impersonation
    (phase 8), so clinic-scoped views must not be reachable for them.
    """
    if user.clinic_id is None:
        raise NoClinicScopeError
    return user.clinic_id
