"""Initiales Schema: projects, project_events

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "processing",
                "completed",
                "failed",
                name="project_status",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("current_step", sa.String(length=50), nullable=True),
        sa.Column("artifacts", JsonType, nullable=False),
        sa.Column("error", JsonType, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_status"), "projects", ["status"])

    op.create_table(
        "project_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("producer", sa.String(length=50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_events_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_project_events_event_id")),
    )
    op.create_index(op.f("ix_project_events_project_id"), "project_events", ["project_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_project_events_project_id"), table_name="project_events")
    op.drop_table("project_events")
    op.drop_index(op.f("ix_projects_status"), table_name="projects")
    op.drop_table("projects")
