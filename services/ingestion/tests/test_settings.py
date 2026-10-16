from pathlib import Path

import pytest
from app.settings import Settings


def test_settings_read_ingestion_environment(monkeypatch, tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    models = tmp_path / "models"
    review_runs = tmp_path / "ingestion" / "runs"
    migrations = tmp_path / "migrations"
    monkeypatch.setenv("INGESTION_DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333/")
    monkeypatch.setenv("INGESTION_DOCUMENT_STORAGE_DIR", str(documents))
    monkeypatch.setenv("INGESTION_MODEL_CACHE_DIR", str(models))
    monkeypatch.setenv("INGESTION_REVIEW_RUNS_CACHE_DIR", str(review_runs))
    monkeypatch.setenv("INGESTION_MIGRATIONS_DIR", str(migrations))
    monkeypatch.setenv("INGESTION_APPROVED_BY", "  document owner  ")
    monkeypatch.setenv("INGESTION_VISION_MODEL", "configurable-vision-model")
    monkeypatch.setenv("INGESTION_VISION_TEMPERATURE", "0.1")
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_JUDGE_MODEL", "configurable-judge-model")
    monkeypatch.setenv("INGESTION_JUDGE_TEMPERATURE", "0.2")
    monkeypatch.setenv("INGESTION_RESOLVER_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_RESOLVER_MODEL", "configurable-resolver-model")
    monkeypatch.setenv("INGESTION_RESOLVER_TEMPERATURE", "0.3")

    settings = Settings.from_environment()
    settings.prepare_directories()

    assert settings.database_url == "postgresql://test"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.qdrant_collection == "liveqna_chunks"
    assert settings.approved_by == "document owner"
    assert settings.text_max_bytes == 33_554_432
    assert settings.text_max_lines == 500_000
    assert settings.text_max_line_characters == 200_000
    assert settings.text_max_blocks == 100_000
    assert settings.text_max_block_characters == 2_097_152
    assert settings.vision_provider == "openai"
    assert settings.vision_model == "configurable-vision-model"
    assert settings.vision_temperature == 0.1
    assert settings.vision_detail == "high"
    assert settings.vision_prompt_version == "pptx-slide-v4"
    assert settings.vision_max_concurrency == 4
    assert settings.review_provider == "openai"
    assert settings.review_model == "configurable-judge-model"
    assert settings.review_temperature == 0.2
    assert settings.review_prompt_version == "pptx-slide-review-v1"
    assert settings.resolver_provider == "openai"
    assert settings.resolver_model == "configurable-resolver-model"
    assert settings.resolver_temperature == 0.3
    assert settings.pdf_render_dpi == 144
    assert settings.pdf_vision_prompt_version == "pdf-page-v2"
    assert settings.pdf_review_prompt_version == "pdf-page-review-v1"
    assert settings.pdf_resolver_prompt_version == "pdf-human-resolution-v1"
    assert settings.docx_soffice_binary == "soffice"
    assert settings.docx_render_dpi == 144
    assert settings.docx_vision_prompt_version == "docx-page-v1"
    assert settings.docx_review_prompt_version == "docx-page-review-v1"
    assert settings.docx_resolver_prompt_version == "docx-human-resolution-v1"
    assert settings.docx_revision_policy == "final"
    assert settings.docx_max_pages == 500
    assert settings.xlsx_visual_mode == "selective"
    assert settings.xlsx_vision_prompt_version == "xlsx-worksheet-v1"
    assert settings.xlsx_review_prompt_version == "xlsx-worksheet-review-v1"
    assert settings.xlsx_resolver_prompt_version == "xlsx-human-resolution-v1"
    assert settings.xlsx_max_cells == 1_000_000
    assert documents.is_dir()
    assert models.is_dir()
    assert review_runs.is_dir()


def test_settings_require_vision_model_environment_variable(monkeypatch) -> None:
    monkeypatch.delenv("INGESTION_VISION_MODEL", raising=False)

    with pytest.raises(
        ValueError, match="INGESTION_VISION_MODEL must not be empty"
    ):
        Settings.from_environment()


def test_explicit_judge_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INGESTION_VISION_MODEL", "vision-model")
    monkeypatch.setenv("INGESTION_DOCUMENT_STORAGE_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("INGESTION_MODEL_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "ollama")
    monkeypatch.setenv("INGESTION_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("INGESTION_JUDGE_DETAIL", "low")
    monkeypatch.setenv("INGESTION_JUDGE_PROMPT_VERSION", "judge-prompt-v2")
    monkeypatch.setenv("INGESTION_JUDGE_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("INGESTION_JUDGE_MAX_RETRIES", "3")
    monkeypatch.setenv("INGESTION_JUDGE_MAX_OUTPUT_TOKENS", "1800")
    monkeypatch.setenv("INGESTION_RESOLVER_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_RESOLVER_MODEL", "resolver-model")

    settings = Settings.from_environment()

    assert settings.review_provider == "ollama"
    assert settings.review_model == "judge-model"
    assert settings.review_detail == "low"
    assert settings.review_prompt_version == "judge-prompt-v2"
    assert settings.review_timeout_seconds == 45
    assert settings.review_max_retries == 3
    assert settings.review_max_output_tokens == 1800


def test_settings_require_judge_model_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("INGESTION_VISION_MODEL", "vision-model")
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "openai")
    monkeypatch.delenv("INGESTION_JUDGE_MODEL", raising=False)

    with pytest.raises(
        ValueError, match="INGESTION_JUDGE_MODEL must not be empty"
    ):
        Settings.from_environment()


def test_settings_require_resolver_model_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("INGESTION_VISION_MODEL", "vision-model")
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("INGESTION_RESOLVER_PROVIDER", "openai")
    monkeypatch.delenv("INGESTION_RESOLVER_MODEL", raising=False)

    with pytest.raises(
        ValueError, match="INGESTION_RESOLVER_MODEL must not be empty"
    ):
        Settings.from_environment()


def test_temperature_must_be_between_zero_and_two(monkeypatch) -> None:
    monkeypatch.setenv("INGESTION_VISION_MODEL", "vision-model")
    monkeypatch.setenv("INGESTION_VISION_TEMPERATURE", "2.1")
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("INGESTION_RESOLVER_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_RESOLVER_MODEL", "resolver-model")

    with pytest.raises(
        ValueError,
        match="INGESTION_VISION_TEMPERATURE must be between 0 and 2",
    ):
        Settings.from_environment()


def test_docx_revision_policy_is_validated(monkeypatch) -> None:
    monkeypatch.setenv("INGESTION_VISION_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_VISION_MODEL", "vision-model")
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("INGESTION_RESOLVER_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_RESOLVER_MODEL", "resolver-model")
    monkeypatch.setenv("INGESTION_DOCX_REVISION_POLICY", "accept_everything")

    with pytest.raises(
        ValueError,
        match="INGESTION_DOCX_REVISION_POLICY must be final or original",
    ):
        Settings.from_environment()


def test_text_limits_must_be_positive(monkeypatch) -> None:
    monkeypatch.setenv("INGESTION_VISION_MODEL", "vision-model")
    monkeypatch.setenv("INGESTION_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("INGESTION_RESOLVER_PROVIDER", "openai")
    monkeypatch.setenv("INGESTION_RESOLVER_MODEL", "resolver-model")
    monkeypatch.setenv("INGESTION_TEXT_MAX_BLOCKS", "0")

    with pytest.raises(
        ValueError, match="INGESTION_TEXT_MAX_BLOCKS must be positive"
    ):
        Settings.from_environment()
