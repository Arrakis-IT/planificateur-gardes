"""user accepted_terms_at

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL for existing users: they never went through the invitation checkbox.
    op.add_column('user', sa.Column('accepted_terms_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'accepted_terms_at')
