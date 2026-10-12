from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from .migrations import apply_migrations
from .settings import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
            timeout=settings.dependency_timeout_seconds,
            open=False,
            name="liveqna-ingestion",
        )
        self.applied_migrations: list[str] = []

    async def open(self) -> None:
        await self._pool.open(
            wait=True, timeout=self._settings.dependency_timeout_seconds
        )
        self.applied_migrations = await apply_migrations(
            self._pool, self._settings.migrations_dir
        )

    async def ping(self) -> bool:
        async with self._pool.connection(
            timeout=self._settings.dependency_timeout_seconds
        ) as connection:
            cursor = await connection.execute("SELECT 1")
            row = await cursor.fetchone()
        return row == (1,)

    async def close(self) -> None:
        await self._pool.close()
