# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""SQLAlchemy models. Import every model here so Alembic sees the full metadata."""

from app.models.base import Base

__all__ = ["Base"]
