"""SQLAlchemy-2.0-Modelle. Schemaänderungen immer über Alembic-Migrationen (migrations/)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB auf PostgreSQL, generisches JSON sonst (SQLite in Unit-Tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")
# BIGSERIAL auf PostgreSQL; SQLite kennt Autoincrement nur für INTEGER PRIMARY KEY.
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    # Feste Constraint-Namen: Migrationen und Modelle bleiben deckungsgleich (alembic check).
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class ProjectStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            name="project_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum: [m.value for m in enum],
        ),
        default=ProjectStatus.PROCESSING,
        index=True,
    )
    current_step: Mapped[str | None] = mapped_column(String(50))
    artifacts: Mapped[dict[str, str]] = mapped_column(JsonType, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list[ProjectEvent]] = relationship(
        back_populates="project",
        order_by="ProjectEvent.occurred_at",
        cascade="all, delete-orphan",
    )


class ProjectEvent(Base):
    """Protokoll aller Pipeline-Events eines Projekts (Timeline im Frontend)."""

    __tablename__ = "project_events"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(50))
    producer: Mapped[str] = mapped_column(String(50))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)

    project: Mapped[Project] = relationship(back_populates="events")
