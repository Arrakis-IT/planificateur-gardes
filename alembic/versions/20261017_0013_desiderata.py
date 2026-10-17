"""desiderata days and assignment desiderata_conflict

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-17 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('desiderata_day',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('clinic_id', sa.Uuid(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['clinic_id'], ['clinic.id'], name=op.f('fk_desiderata_day_clinic_id_clinic'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_desiderata_day_user_id_user'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_desiderata_day')),
    sa.UniqueConstraint('user_id', 'date', name=op.f('uq_desiderata_day_user_id_date'))
    )
    op.create_index('ix_desiderata_day_clinic_id_date', 'desiderata_day', ['clinic_id', 'date'], unique=False)
    # Existing assignments predate desiderata: no conflict.
    op.add_column(
        'assignment',
        sa.Column(
            'desiderata_conflict',
            sa.Enum('generated', 'manual', name='desiderata_conflict', native_enum=False, length=20),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('assignment', 'desiderata_conflict')
    op.drop_index('ix_desiderata_day_clinic_id_date', table_name='desiderata_day')
    op.drop_table('desiderata_day')
