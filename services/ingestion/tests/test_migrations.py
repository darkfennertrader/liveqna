from pathlib import Path

import pytest

from app.migrations import MigrationError, load_migrations


def test_load_migrations_orders_and_hashes_files(tmp_path: Path) -> None:
    (tmp_path / "002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")

    migrations = load_migrations(tmp_path)

    assert [item.version for item in migrations] == ["001", "002"]
    assert [item.name for item in migrations] == ["first", "second"]
    assert all(len(item.checksum) == 64 for item in migrations)


def test_load_migrations_rejects_invalid_filename(tmp_path: Path) -> None:
    (tmp_path / "initial.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationError, match="invalid migration filename"):
        load_migrations(tmp_path)


def test_load_migrations_requires_at_least_one_file(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="no migrations found"):
        load_migrations(tmp_path)
