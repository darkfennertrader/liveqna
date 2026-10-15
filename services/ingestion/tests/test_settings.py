from pathlib import Path

import pytest

from app.settings import Settings


def test_settings_read_ingestion_environment(monkeypatch, tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    models = tmp_path / "models"
    migrations = tmp_path / "migrations"
    monkeypatch.setenv("INGESTION_DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333/")
    monkeypatch.setenv("INGESTION_DOCUMENT_STORAGE_DIR", str(documents))
    monkeypatch.setenv("INGESTION_MODEL_CACHE_DIR", str(models))
    monkeypatch.setenv("INGESTION_MIGRATIONS_DIR", str(migrations))
    monkeypatch.setenv("INGESTION_APPROVED_BY", "  document owner  ")
    monkeypatch.setenv("INGESTION_VISION_MODEL", "configurable-vision-model")

    settings = Settings.from_environment()
    settings.prepare_directories()

    assert settings.database_url == "postgresql://test"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.qdrant_collection == "liveqna_chunks"
    assert settings.approved_by == "document owner"
    assert settings.vision_provider == "openai"
    assert settings.vision_model == "configurable-vision-model"
    assert settings.vision_detail == "high"
    assert settings.vision_prompt_version == "pptx-slide-v4"
    assert settings.vision_max_concurrency == 4
    assert settings.review_provider == "openai"
    assert settings.review_model == "configurable-vision-model"
    assert settings.review_prompt_version == "pptx-slide-review-v1"
    assert settings.pdf_pdftoppm_binary == "pdftoppm"
    assert settings.pdf_render_dpi == 144
    assert settings.pdf_vision_prompt_version == "pdf-page-v1"
    assert documents.is_dir()
    assert models.is_dir()


def test_settings_require_vision_model_environment_variable(monkeypatch) -> None:
    monkeypatch.delenv("INGESTION_VISION_MODEL", raising=False)

    with pytest.raises(
        ValueError, match="INGESTION_VISION_MODEL must not be empty"
    ):
        Settings.from_environment()
