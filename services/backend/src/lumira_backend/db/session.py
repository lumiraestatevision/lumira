from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from lumira_backend.db.models import Base


class Database:
    """Engine und Session-Factory. Die Engine verbindet sich erst beim ersten Zugriff."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def create_all(self) -> None:
        """Nur für Tests – im Betrieb legt Alembic das Schema an."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
