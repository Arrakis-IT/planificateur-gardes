"""Enable PostgreSQL extensions required by the schema.

citext is used for case-insensitive user emails. It is a trusted extension
(PostgreSQL 13+), so the database owner can create it without superuser rights.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS citext")
