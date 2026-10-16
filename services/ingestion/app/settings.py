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
    text_max_bytes: int
    text_max_lines: int
    text_max_line_characters: int
    text_max_blocks: int
    text_max_block_characters: int
    pdf_render_dpi: int
    pdf_vision_prompt_version: str
    pdf_review_prompt_version: str
    pdf_resolver_prompt_version: str
    docx_soffice_binary: str
    docx_render_dpi: int
    docx_render_timeout_seconds: float
    docx_vision_prompt_version: str
    docx_review_prompt_version: str
    docx_resolver_prompt_version: str
    docx_revision_policy: str
    docx_max_package_entries: int
    docx_max_compressed_bytes: int
    docx_max_uncompressed_bytes: int
    docx_max_compression_ratio: float
    docx_max_paragraphs: int
    docx_max_tables: int
    docx_max_pages: int
    pptx_soffice_binary: str
    pptx_pdftoppm_binary: str
    pptx_render_dpi: int
    pptx_render_timeout_seconds: float
    xlsx_soffice_binary: str
    xlsx_uno_python: str
    xlsx_helper_script: str
    xlsx_render_dpi: int
    xlsx_render_timeout_seconds: float
    xlsx_max_render_pages_per_sheet: int
    xlsx_visual_mode: str
    xlsx_vision_prompt_version: str
    xlsx_review_prompt_version: str
    xlsx_resolver_prompt_version: str
    xlsx_max_package_entries: int
    xlsx_max_uncompressed_bytes: int
    xlsx_max_compression_ratio: float
    xlsx_max_sheets: int
    xlsx_max_cells: int
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
    def from_environment(cls) -> Settings:
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
            text_max_bytes=int(
                os.environ.get("INGESTION_TEXT_MAX_BYTES", "33554432")
            ),
            text_max_lines=int(
                os.environ.get("INGESTION_TEXT_MAX_LINES", "500000")
            ),
            text_max_line_characters=int(
                os.environ.get(
                    "INGESTION_TEXT_MAX_LINE_CHARACTERS", "200000"
                )
            ),
            text_max_blocks=int(
                os.environ.get("INGESTION_TEXT_MAX_BLOCKS", "100000")
            ),
            text_max_block_characters=int(
                os.environ.get(
                    "INGESTION_TEXT_MAX_BLOCK_CHARACTERS", "2097152"
                )
            ),
            pdf_render_dpi=int(
                os.environ.get("INGESTION_PDF_RENDER_DPI", "144")
            ),
            pdf_vision_prompt_version=os.environ.get(
                "INGESTION_PDF_VISION_PROMPT_VERSION", "pdf-page-v2"
            ).strip()
            or "pdf-page-v2",
            pdf_review_prompt_version=os.environ.get(
                "INGESTION_PDF_JUDGE_PROMPT_VERSION", "pdf-page-review-v1"
            ).strip()
            or "pdf-page-review-v1",
            pdf_resolver_prompt_version=os.environ.get(
                "INGESTION_PDF_RESOLVER_PROMPT_VERSION",
                "pdf-human-resolution-v1",
            ).strip()
            or "pdf-human-resolution-v1",
            docx_soffice_binary=os.environ.get(
                "INGESTION_DOCX_SOFFICE_BINARY", "soffice"
            ).strip()
            or "soffice",
            docx_render_dpi=int(
                os.environ.get("INGESTION_DOCX_RENDER_DPI", "144")
            ),
            docx_render_timeout_seconds=float(
                os.environ.get("INGESTION_DOCX_RENDER_TIMEOUT_SECONDS", "180")
            ),
            docx_vision_prompt_version=os.environ.get(
                "INGESTION_DOCX_VISION_PROMPT_VERSION", "docx-page-v1"
            ).strip()
            or "docx-page-v1",
            docx_review_prompt_version=os.environ.get(
                "INGESTION_DOCX_JUDGE_PROMPT_VERSION", "docx-page-review-v1"
            ).strip()
            or "docx-page-review-v1",
            docx_resolver_prompt_version=os.environ.get(
                "INGESTION_DOCX_RESOLVER_PROMPT_VERSION",
                "docx-human-resolution-v1",
            ).strip()
            or "docx-human-resolution-v1",
            docx_revision_policy=os.environ.get(
                "INGESTION_DOCX_REVISION_POLICY", "final"
            ).strip().lower(),
            docx_max_package_entries=int(
                os.environ.get("INGESTION_DOCX_MAX_PACKAGE_ENTRIES", "20000")
            ),
            docx_max_compressed_bytes=int(
                os.environ.get(
                    "INGESTION_DOCX_MAX_COMPRESSED_BYTES", "134217728"
                )
            ),
            docx_max_uncompressed_bytes=int(
                os.environ.get(
                    "INGESTION_DOCX_MAX_UNCOMPRESSED_BYTES", "536870912"
                )
            ),
            docx_max_compression_ratio=float(
                os.environ.get("INGESTION_DOCX_MAX_COMPRESSION_RATIO", "100")
            ),
            docx_max_paragraphs=int(
                os.environ.get("INGESTION_DOCX_MAX_PARAGRAPHS", "200000")
            ),
            docx_max_tables=int(
                os.environ.get("INGESTION_DOCX_MAX_TABLES", "20000")
            ),
            docx_max_pages=int(
                os.environ.get("INGESTION_DOCX_MAX_PAGES", "500")
            ),
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
            xlsx_soffice_binary=os.environ.get(
                "INGESTION_XLSX_SOFFICE_BINARY", "soffice"
            ).strip()
            or "soffice",
            xlsx_uno_python=os.environ.get(
                "INGESTION_XLSX_UNO_PYTHON", "/usr/bin/python3"
            ).strip()
            or "/usr/bin/python3",
            xlsx_helper_script=os.environ.get(
                "INGESTION_XLSX_HELPER_SCRIPT",
                "/app/scripts/xlsx_libreoffice.py",
            ).strip()
            or "/app/scripts/xlsx_libreoffice.py",
            xlsx_render_dpi=int(
                os.environ.get("INGESTION_XLSX_RENDER_DPI", "144")
            ),
            xlsx_render_timeout_seconds=float(
                os.environ.get("INGESTION_XLSX_RENDER_TIMEOUT_SECONDS", "180")
            ),
            xlsx_max_render_pages_per_sheet=int(
                os.environ.get(
                    "INGESTION_XLSX_MAX_RENDER_PAGES_PER_SHEET", "8"
                )
            ),
            xlsx_visual_mode=os.environ.get(
                "INGESTION_XLSX_VISUAL_MODE", "selective"
            ).strip().lower(),
            xlsx_vision_prompt_version=os.environ.get(
                "INGESTION_XLSX_VISION_PROMPT_VERSION", "xlsx-worksheet-v1"
            ).strip()
            or "xlsx-worksheet-v1",
            xlsx_review_prompt_version=os.environ.get(
                "INGESTION_XLSX_JUDGE_PROMPT_VERSION",
                "xlsx-worksheet-review-v1",
            ).strip()
            or "xlsx-worksheet-review-v1",
            xlsx_resolver_prompt_version=os.environ.get(
                "INGESTION_XLSX_RESOLVER_PROMPT_VERSION",
                "xlsx-human-resolution-v1",
            ).strip()
            or "xlsx-human-resolution-v1",
            xlsx_max_package_entries=int(
                os.environ.get("INGESTION_XLSX_MAX_PACKAGE_ENTRIES", "20000")
            ),
            xlsx_max_uncompressed_bytes=int(
                os.environ.get(
                    "INGESTION_XLSX_MAX_UNCOMPRESSED_BYTES", "536870912"
                )
            ),
            xlsx_max_compression_ratio=float(
                os.environ.get("INGESTION_XLSX_MAX_COMPRESSION_RATIO", "100")
            ),
            xlsx_max_sheets=int(
                os.environ.get("INGESTION_XLSX_MAX_SHEETS", "200")
            ),
            xlsx_max_cells=int(
                os.environ.get("INGESTION_XLSX_MAX_CELLS", "1000000")
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
        for name, value in (
            ("INGESTION_TEXT_MAX_BYTES", settings.text_max_bytes),
            ("INGESTION_TEXT_MAX_LINES", settings.text_max_lines),
            (
                "INGESTION_TEXT_MAX_LINE_CHARACTERS",
                settings.text_max_line_characters,
            ),
            ("INGESTION_TEXT_MAX_BLOCKS", settings.text_max_blocks),
            (
                "INGESTION_TEXT_MAX_BLOCK_CHARACTERS",
                settings.text_max_block_characters,
            ),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if settings.pdf_render_dpi <= 0:
            raise ValueError("INGESTION_PDF_RENDER_DPI must be positive")
        if settings.docx_render_dpi <= 0:
            raise ValueError("INGESTION_DOCX_RENDER_DPI must be positive")
        if settings.docx_render_timeout_seconds <= 0:
            raise ValueError(
                "INGESTION_DOCX_RENDER_TIMEOUT_SECONDS must be positive"
            )
        if settings.docx_revision_policy not in {"final", "original"}:
            raise ValueError(
                "INGESTION_DOCX_REVISION_POLICY must be final or original"
            )
        for name, value in (
            ("INGESTION_DOCX_MAX_PACKAGE_ENTRIES", settings.docx_max_package_entries),
            ("INGESTION_DOCX_MAX_COMPRESSED_BYTES", settings.docx_max_compressed_bytes),
            ("INGESTION_DOCX_MAX_UNCOMPRESSED_BYTES", settings.docx_max_uncompressed_bytes),
            ("INGESTION_DOCX_MAX_PARAGRAPHS", settings.docx_max_paragraphs),
            ("INGESTION_DOCX_MAX_TABLES", settings.docx_max_tables),
            ("INGESTION_DOCX_MAX_PAGES", settings.docx_max_pages),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if settings.docx_max_compression_ratio <= 0:
            raise ValueError(
                "INGESTION_DOCX_MAX_COMPRESSION_RATIO must be positive"
            )
        if settings.pptx_render_dpi <= 0:
            raise ValueError("INGESTION_PPTX_RENDER_DPI must be positive")
        if settings.xlsx_render_dpi <= 0:
            raise ValueError("INGESTION_XLSX_RENDER_DPI must be positive")
        if settings.xlsx_visual_mode not in {"selective", "all"}:
            raise ValueError(
                "INGESTION_XLSX_VISUAL_MODE must be selective or all"
            )
        for name, value in (
            (
                "INGESTION_XLSX_MAX_RENDER_PAGES_PER_SHEET",
                settings.xlsx_max_render_pages_per_sheet,
            ),
            ("INGESTION_XLSX_MAX_PACKAGE_ENTRIES", settings.xlsx_max_package_entries),
            ("INGESTION_XLSX_MAX_UNCOMPRESSED_BYTES", settings.xlsx_max_uncompressed_bytes),
            ("INGESTION_XLSX_MAX_SHEETS", settings.xlsx_max_sheets),
            ("INGESTION_XLSX_MAX_CELLS", settings.xlsx_max_cells),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if settings.xlsx_max_compression_ratio <= 0:
            raise ValueError(
                "INGESTION_XLSX_MAX_COMPRESSION_RATIO must be positive"
            )
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
