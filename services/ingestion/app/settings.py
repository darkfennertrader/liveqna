from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    qdrant_url: str
    document_storage_dir: Path
    model_cache_dir: Path
    migrations_dir: Path
    database_pool_min_size: int
    database_pool_max_size: int
    dependency_timeout_seconds: float

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_url=os.environ.get(
                "INGESTION_DATABASE_URL",
                "postgresql://liveqna:liveqna-local@postgres:5432/liveqna",
            ),
            qdrant_url=os.environ.get(
                "QDRANT_URL", "http://qdrant:6333"
            ).rstrip("/"),
            document_storage_dir=Path(
                os.environ.get("INGESTION_DOCUMENT_STORAGE_DIR", "/data/documents")
            ),
            model_cache_dir=Path(
                os.environ.get("INGESTION_MODEL_CACHE_DIR", "/models")
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
        )

    def prepare_directories(self) -> None:
        self.document_storage_dir.mkdir(parents=True, exist_ok=True)
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
