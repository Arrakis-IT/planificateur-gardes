"""user ical_token

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No token is generated here: users create theirs from the settings page.
    op.add_column('user', sa.Column('ical_token', sa.Text(), nullable=True))
    op.create_unique_constraint(op.f('uq_user_ical_token'), 'user', ['ical_token'])


def downgrade() -> None:
    op.drop_constraint(op.f('uq_user_ical_token'), 'user', type_='unique')
    op.drop_column('user', 'ical_token')
