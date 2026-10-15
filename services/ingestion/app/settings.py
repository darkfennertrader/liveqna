from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _optional_temperature(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    value = float(raw)
    if not 0 <= value <= 2:
        raise ValueError(f"{name} must be between 0 and 2")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    qdrant_url: str
    qdrant_collection: str
    document_storage_dir: Path
    model_cache_dir: Path
    review_runs_cache_dir: Path
    migrations_dir: Path
    database_pool_min_size: int
    database_pool_max_size: int
    dependency_timeout_seconds: float
    approved_by: str
    pdf_pdftoppm_binary: str
    pdf_render_dpi: int
    pdf_render_timeout_seconds: float
    pdf_vision_prompt_version: str
    pptx_soffice_binary: str
    pptx_pdftoppm_binary: str
    pptx_render_dpi: int
    pptx_render_timeout_seconds: float
    vision_provider: str
    vision_model: str
    vision_temperature: float | None
    vision_detail: str
    vision_prompt_version: str
    vision_timeout_seconds: float
    vision_max_retries: int
    vision_max_output_tokens: int
    vision_max_concurrency: int
    review_provider: str
    review_model: str
    review_temperature: float | None
    review_detail: str
    review_prompt_version: str
    review_timeout_seconds: float
    review_max_retries: int
    review_max_output_tokens: int
    resolver_provider: str
    resolver_model: str
    resolver_temperature: float | None
    resolver_detail: str
    resolver_prompt_version: str
    resolver_timeout_seconds: float
    resolver_max_retries: int
    resolver_max_output_tokens: int
    openai_api_key: str = field(repr=False)
    ollama_base_url: str

    @classmethod
    def from_environment(cls) -> "Settings":
        vision_provider = os.environ.get(
            "INGESTION_VISION_PROVIDER", "openai"
        ).strip().lower()
        if vision_provider not in {"openai", "ollama"}:
            raise ValueError(
                "INGESTION_VISION_PROVIDER must be 'openai' or 'ollama'"
            )
        vision_model = os.environ.get("INGESTION_VISION_MODEL", "").strip()
        if not vision_model:
            raise ValueError("INGESTION_VISION_MODEL must not be empty")
        vision_detail = os.environ.get(
            "INGESTION_VISION_DETAIL", "high"
        ).strip().lower()
        if vision_detail not in {"low", "high", "original", "auto"}:
            raise ValueError(
                "INGESTION_VISION_DETAIL must be low, high, original, or auto"
            )
        review_provider = os.environ.get(
            "INGESTION_JUDGE_PROVIDER", ""
        ).strip().lower()
        if review_provider not in {"openai", "ollama"}:
            raise ValueError(
                "INGESTION_JUDGE_PROVIDER must be 'openai' or 'ollama'"
            )
        review_model = os.environ.get("INGESTION_JUDGE_MODEL", "").strip()
        if not review_model:
            raise ValueError("INGESTION_JUDGE_MODEL must not be empty")
        review_detail = os.environ.get(
            "INGESTION_JUDGE_DETAIL", "high"
        ).strip().lower()
        if review_detail not in {"low", "high", "original", "auto"}:
            raise ValueError(
                "INGESTION_JUDGE_DETAIL must be low, high, original, or auto"
            )
        resolver_provider = os.environ.get(
            "INGESTION_RESOLVER_PROVIDER", ""
        ).strip().lower()
        if resolver_provider not in {"openai", "ollama"}:
            raise ValueError(
                "INGESTION_RESOLVER_PROVIDER must be 'openai' or 'ollama'"
            )
        resolver_model = os.environ.get("INGESTION_RESOLVER_MODEL", "").strip()
        if not resolver_model:
            raise ValueError("INGESTION_RESOLVER_MODEL must not be empty")
        resolver_detail = os.environ.get(
            "INGESTION_RESOLVER_DETAIL", "high"
        ).strip().lower()
        if resolver_detail not in {"low", "high", "original", "auto"}:
            raise ValueError(
                "INGESTION_RESOLVER_DETAIL must be low, high, original, or auto"
            )
        settings = cls(
            database_url=os.environ.get(
                "INGESTION_DATABASE_URL",
                "postgresql://liveqna:liveqna-local@postgres:5432/liveqna",
            ),
            qdrant_url=os.environ.get(
                "QDRANT_URL", "http://qdrant:6333"
            ).rstrip("/"),
            qdrant_collection=os.environ.get(
                "QDRANT_COLLECTION", "liveqna_chunks"
            ).strip()
            or "liveqna_chunks",
            document_storage_dir=Path(
                os.environ.get("INGESTION_DOCUMENT_STORAGE_DIR", "/data/documents")
            ),
            model_cache_dir=Path(
                os.environ.get("INGESTION_MODEL_CACHE_DIR", "/models")
            ),
            review_runs_cache_dir=Path(
                os.environ.get(
                    "INGESTION_REVIEW_RUNS_CACHE_DIR",
                    "/cache/ingestion/runs",
                )
            ),
            migrations_dir=Path(
                os.environ.get(
                    "INGESTION_MIGRATIONS_DIR",
                    str(Path(__file__).resolve().parents[1] / "migrations"),
                )
            ),
            database_pool_min_size=int(
                os.environ.get("INGESTION_DB_POOL_MIN_SIZE", "1")
            ),
            database_pool_max_size=int(
                os.environ.get("INGESTION_DB_POOL_MAX_SIZE", "5")
            ),
            dependency_timeout_seconds=float(
                os.environ.get("INGESTION_DEPENDENCY_TIMEOUT_SECONDS", "10")
            ),
            approved_by=os.environ.get(
                "INGESTION_APPROVED_BY", "local-owner"
            ).strip()
            or "local-owner",
            pdf_pdftoppm_binary=os.environ.get(
                "INGESTION_PDF_PDFTOPPM_BINARY", "pdftoppm"
            ).strip()
            or "pdftoppm",
            pdf_render_dpi=int(
                os.environ.get("INGESTION_PDF_RENDER_DPI", "144")
            ),
            pdf_render_timeout_seconds=float(
                os.environ.get("INGESTION_PDF_RENDER_TIMEOUT_SECONDS", "180")
            ),
            pdf_vision_prompt_version=os.environ.get(
                "INGESTION_PDF_VISION_PROMPT_VERSION", "pdf-page-v1"
            ).strip()
            or "pdf-page-v1",
            pptx_soffice_binary=os.environ.get(
                "INGESTION_PPTX_SOFFICE_BINARY", "soffice"
            ).strip()
            or "soffice",
            pptx_pdftoppm_binary=os.environ.get(
                "INGESTION_PPTX_PDFTOPPM_BINARY", "pdftoppm"
            ).strip()
            or "pdftoppm",
            pptx_render_dpi=int(
                os.environ.get("INGESTION_PPTX_RENDER_DPI", "144")
            ),
            pptx_render_timeout_seconds=float(
                os.environ.get("INGESTION_PPTX_RENDER_TIMEOUT_SECONDS", "180")
            ),
            vision_provider=vision_provider,
            vision_model=vision_model,
            vision_temperature=_optional_temperature(
                "INGESTION_VISION_TEMPERATURE"
            ),
            vision_detail=vision_detail,
            vision_prompt_version=os.environ.get(
                "INGESTION_VISION_PROMPT_VERSION", "pptx-slide-v4"
            ).strip()
            or "pptx-slide-v4",
            vision_timeout_seconds=float(
                os.environ.get("INGESTION_VISION_TIMEOUT_SECONDS", "120")
            ),
            vision_max_retries=int(
                os.environ.get("INGESTION_VISION_MAX_RETRIES", "1")
            ),
            vision_max_output_tokens=int(
                os.environ.get("INGESTION_VISION_MAX_OUTPUT_TOKENS", "4000")
            ),
            vision_max_concurrency=int(
                os.environ.get("INGESTION_VISION_MAX_CONCURRENCY", "4")
            ),
            review_provider=review_provider,
            review_model=review_model,
            review_temperature=_optional_temperature(
                "INGESTION_JUDGE_TEMPERATURE"
            ),
            review_detail=review_detail,
            review_prompt_version=os.environ.get(
                "INGESTION_JUDGE_PROMPT_VERSION", "pptx-slide-review-v1"
            ).strip()
            or "pptx-slide-review-v1",
            review_timeout_seconds=float(
                os.environ.get("INGESTION_JUDGE_TIMEOUT_SECONDS", "120")
            ),
            review_max_retries=int(
                os.environ.get("INGESTION_JUDGE_MAX_RETRIES", "1")
            ),
            review_max_output_tokens=int(
                os.environ.get("INGESTION_JUDGE_MAX_OUTPUT_TOKENS", "2500")
            ),
            resolver_provider=resolver_provider,
            resolver_model=resolver_model,
            resolver_temperature=_optional_temperature(
                "INGESTION_RESOLVER_TEMPERATURE"
            ),
            resolver_detail=resolver_detail,
            resolver_prompt_version=os.environ.get(
                "INGESTION_RESOLVER_PROMPT_VERSION",
                "pptx-human-resolution-v1",
            ).strip()
            or "pptx-human-resolution-v1",
            resolver_timeout_seconds=float(
                os.environ.get("INGESTION_RESOLVER_TIMEOUT_SECONDS", "120")
            ),
            resolver_max_retries=int(
                os.environ.get("INGESTION_RESOLVER_MAX_RETRIES", "1")
            ),
            resolver_max_output_tokens=int(
                os.environ.get("INGESTION_RESOLVER_MAX_OUTPUT_TOKENS", "3000")
            ),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            ollama_base_url=os.environ.get(
                "OLLAMA_BASE_URL", "http://host.docker.internal:11434"
            ).rstrip("/"),
        )
        if settings.pdf_render_dpi <= 0:
            raise ValueError("INGESTION_PDF_RENDER_DPI must be positive")
        if settings.pptx_render_dpi <= 0:
            raise ValueError("INGESTION_PPTX_RENDER_DPI must be positive")
        if settings.vision_max_concurrency <= 0:
            raise ValueError(
                "INGESTION_VISION_MAX_CONCURRENCY must be positive"
            )
        if settings.review_max_output_tokens <= 0:
            raise ValueError(
                "INGESTION_JUDGE_MAX_OUTPUT_TOKENS must be positive"
            )
        if settings.resolver_max_output_tokens <= 0:
            raise ValueError(
                "INGESTION_RESOLVER_MAX_OUTPUT_TOKENS must be positive"
            )
        return settings

    def prepare_directories(self) -> None:
        self.document_storage_dir.mkdir(parents=True, exist_ok=True)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
        self.review_runs_cache_dir.mkdir(parents=True, exist_ok=True)
