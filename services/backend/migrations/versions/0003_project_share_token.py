"""projects.share_token: geheimer Schlüssel für den Kunden-Link (nur Ansicht)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: ohne Schlüssel ist ein Projekt nicht geteilt.
    op.add_column("projects", sa.Column("share_token", sa.String(length=64), nullable=True))
    op.create_unique_constraint(op.f("uq_projects_share_token"), "projects", ["share_token"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_projects_share_token"), "projects", type_="unique")
    op.drop_column("projects", "share_token")
