"""clinic color_palette

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-16 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing clinics keep the default palette: doctor colors do not change.
    op.add_column(
        'clinic',
        sa.Column(
            'color_palette',
            sa.Enum('default', 'grayscale', 'blue', 'pink_purple', name='color_palette', native_enum=False, length=20),
            server_default='default',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('clinic', 'color_palette')
