from __future__ import annotations

import aiosqlite


class RunRepository:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    async def setup(self) -> None:
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT
                )
                """
            )
            await db.commit()

    async def create(self, run_id: str, created_at: str) -> None:
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                "INSERT INTO orchestration_runs(run_id, created_at, status) VALUES (?, ?, ?)",
                (run_id, created_at, "generating"),
            )
            await db.commit()

    async def update(self, run_id: str, status: str, error: str | None = None) -> None:
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute(
                "UPDATE orchestration_runs SET status = ?, error = ? WHERE run_id = ?",
                (status, error, run_id),
            )
            await db.commit()

    async def exists(self, run_id: str) -> bool:
        async with aiosqlite.connect(self._database_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM orchestration_runs WHERE run_id = ?", (run_id,)
            )
            return await cursor.fetchone() is not None

    async def status(self, run_id: str) -> tuple[str, str | None] | None:
        async with aiosqlite.connect(self._database_path) as db:
            cursor = await db.execute(
                "SELECT status, error FROM orchestration_runs WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            return (row[0], row[1]) if row else None

    async def list_ids(self, limit: int | None = 100) -> list[str]:
        async with aiosqlite.connect(self._database_path) as db:
            query = "SELECT run_id FROM orchestration_runs ORDER BY created_at DESC"
            if limit is None:
                cursor = await db.execute(query)
            else:
                cursor = await db.execute(f"{query} LIMIT ?", (limit,))
            return [row[0] for row in await cursor.fetchall()]

    async def delete_all(self) -> None:
        async with aiosqlite.connect(self._database_path) as db:
            await db.execute("DELETE FROM orchestration_runs")
            await db.commit()

