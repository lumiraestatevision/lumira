"""Alembic-Umgebung (async, asyncpg). URL aus DATABASE_URL bzw. BackendSettings."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from lumira_backend.config import BackendSettings
from lumira_backend.db.models import Base
from lumira_shared import configure_logging

settings = BackendSettings()
configure_logging("migrate", level=settings.log_level, json_logs=settings.log_json)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """SQL-Skript erzeugen statt ausführen: alembic upgrade head --sql"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
