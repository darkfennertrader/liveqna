from pathlib import Path

from app.settings import Settings


def test_settings_read_phase_zero_environment(monkeypatch, tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    models = tmp_path / "models"
    migrations = tmp_path / "migrations"
    monkeypatch.setenv("INGESTION_DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333/")
    monkeypatch.setenv("INGESTION_DOCUMENT_STORAGE_DIR", str(documents))
    monkeypatch.setenv("INGESTION_MODEL_CACHE_DIR", str(models))
    monkeypatch.setenv("INGESTION_MIGRATIONS_DIR", str(migrations))

    settings = Settings.from_environment()
    settings.prepare_directories()

    assert settings.database_url == "postgresql://test"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert documents.is_dir()
    assert models.is_dir()
