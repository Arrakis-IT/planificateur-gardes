"""several active generations per clinic (active = archived_at IS NULL)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-26 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Active now means archived_at IS NULL: every inactive row must carry a timestamp.
    op.execute(
        "UPDATE generation SET archived_at = generated_at "
        "WHERE NOT is_active AND archived_at IS NULL"
    )
    op.execute("UPDATE generation SET is_active = false WHERE archived_at IS NOT NULL")
    op.drop_index('uq_generation_clinic_id_active', table_name='generation', postgresql_where=sa.text('is_active'))
    op.create_index('ix_generation_clinic_id_open', 'generation', ['clinic_id'], unique=False, postgresql_where=sa.text('archived_at IS NULL'))


def downgrade() -> None:
    # Fails if a clinic has several active generations: archive the extra ones first.
    op.drop_index('ix_generation_clinic_id_open', table_name='generation', postgresql_where=sa.text('archived_at IS NULL'))
    op.create_index('uq_generation_clinic_id_active', 'generation', ['clinic_id'], unique=True, postgresql_where=sa.text('is_active'))
