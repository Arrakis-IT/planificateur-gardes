"""Darken four doctor colors to reach WCAG AA contrast with white initials.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# old → new (same hue, minimal darkening to ≥ 4.5:1 against #ffffff)
COLORS = {
    "#4d8c6b": "#478062",  # sage
    "#8c7a2e": "#84732b",  # mustard
    "#4d8c8c": "#457d7d",  # teal
    "#7a8c4d": "#6a7a43",  # olive
}


def _remap(mapping: dict[str, str]) -> None:
    for old, new in mapping.items():
        op.execute(
            sa.text('UPDATE "user" SET color = :new WHERE lower(color) = :old').bindparams(
                old=old, new=new
            )
        )


def upgrade() -> None:
    _remap(COLORS)


def downgrade() -> None:
    _remap({new: old for old, new in COLORS.items()})
