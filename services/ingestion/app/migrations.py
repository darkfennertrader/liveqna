from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from psycopg_pool import AsyncConnectionPool


MIGRATION_NAME = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """Raised when migration files or applied migration state are invalid."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str
    path: Path


def load_migrations(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"migration directory does not exist: {directory}")

    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {path.name}")
        version = match.group("version")
        if version in versions:
            raise MigrationError(f"duplicate migration version: {version}")
        versions.add(version)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                path=path,
            )
        )
    if not migrations:
        raise MigrationError(f"no migrations found in: {directory}")
    return migrations


async def apply_migrations(
    pool: AsyncConnectionPool, directory: Path
) -> list[str]:
    migrations = load_migrations(directory)
    applied: list[str] = []

    async with pool.connection() as connection:
        await connection.execute("CREATE SCHEMA IF NOT EXISTS ingestion")
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion.schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    for migration in migrations:
        async with pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT checksum
                FROM ingestion.schema_migrations
                WHERE version = %s
                """,
                (migration.version,),
            )
            row = await cursor.fetchone()
            if row is not None:
                if row[0] != migration.checksum:
                    raise MigrationError(
                        "checksum mismatch for applied migration "
                        f"{migration.version}_{migration.name}"
                    )
                continue

            await connection.execute(migration.sql)
            await connection.execute(
                """
                INSERT INTO ingestion.schema_migrations(version, name, checksum)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum),
            )
            applied.append(migration.version)

    return applied
