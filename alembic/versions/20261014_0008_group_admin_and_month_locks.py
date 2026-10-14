"""group admin role and month locks

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user', sa.Column('is_group_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_index('uq_user_clinic_id_group_admin', 'user', ['clinic_id'], unique=True, postgresql_where='is_group_admin')
    # Existing clinics: the first user created in each clinic becomes its group admin
    # (same rule as for new clinics).
    op.execute(
        """
        UPDATE "user" SET is_group_admin = true
        WHERE id IN (
            SELECT DISTINCT ON (clinic_id) id FROM "user"
            WHERE clinic_id IS NOT NULL
            ORDER BY clinic_id, created_at, id
        )
        """
    )
    op.create_table(
        'month_lock',
        sa.Column('clinic_id', sa.Uuid(), nullable=False),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('locked_by', sa.Uuid(), nullable=True),
        sa.Column('locked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['clinic_id'], ['clinic.id'], name=op.f('fk_month_lock_clinic_id_clinic'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['locked_by'], ['user.id'], name=op.f('fk_month_lock_locked_by_user'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_month_lock')),
        sa.UniqueConstraint('clinic_id', 'month', name=op.f('uq_month_lock_clinic_id_month')),
    )


def downgrade() -> None:
    op.drop_table('month_lock')
    op.drop_index('uq_user_clinic_id_group_admin', table_name='user', postgresql_where='is_group_admin')
    op.drop_column('user', 'is_group_admin')
