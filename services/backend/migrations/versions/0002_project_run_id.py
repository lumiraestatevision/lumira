"""projects.run_id: aktueller Pipeline-Durchlauf (für „Neu berechnen“)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: bestehende Projekte haben noch keine Kennung – ihre Events werden wie bisher
    # angenommen, bis sie einmal neu berechnet werden.
    op.add_column("projects", sa.Column("run_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "run_id")
