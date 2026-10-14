"""assignment on-site snapshot

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing generations have no snapshot (the changelog falls back to the
    # current on-site settings for them).
    op.add_column('assignment', sa.Column('onsite', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('assignment', 'onsite')
