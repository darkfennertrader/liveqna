from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool
from psycopg.types.json import Jsonb

from .errors import (
    InvalidLifecycleStateError,
    RegistryConflictError,
    RegistryNotFoundError,
)
from .schemas import (
    DocumentCreate,
    DocumentDeletionResponse,
    DocumentResponse,
    DocumentVersionResponse,
    ExtractionRunResponse,
    ProjectCreate,
    ProjectResponse,
)


VERSION_SELECT = """
SELECT
    version.id,
    version.document_id,
    version.version_label,
    version.status,
    version.file_path,
    version.content_hash,
    version.source_filename,
    version.created_at,
    version.approved_at,
    version.approved_by,
    version.supersedes_version_id,
    version.content_profile,
    version.retrieval_metadata,
    version.llm_metadata,
    COALESCE(run.processing_status, 'FAILED'::ingestion.processing_status)
        AS processing_status
FROM ingestion.document_versions AS version
LEFT JOIN LATERAL (
    SELECT processing_status
    FROM ingestion.ingestion_runs
    WHERE document_version_id = version.id
    ORDER BY created_at DESC
    LIMIT 1
) AS run ON TRUE
"""


class RegistryRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_project(self, payload: ProjectCreate) -> ProjectResponse:
        try:
            async with self._pool.connection() as connection:
                cursor = await connection.execute(
                    """
                    INSERT INTO ingestion.projects(id, name)
                    VALUES (%s, %s)
                    RETURNING id, name, created_at
                    """,
                    (uuid4(), payload.name),
                )
                row = await cursor.fetchone()
        except UniqueViolation as exc:
            raise RegistryConflictError(
                f"project already exists: {payload.name}"
            ) from exc
        assert row is not None
        return ProjectResponse(**row)

    async def list_projects(self) -> list[ProjectResponse]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, name, created_at
                FROM ingestion.projects
                ORDER BY lower(name), id
                """
            )
            rows = await cursor.fetchall()
        return [ProjectResponse(**row) for row in rows]

    async def create_document(
        self, project_id: UUID, payload: DocumentCreate
    ) -> DocumentResponse:
        async with self._pool.connection() as connection:
            project = await connection.execute(
                "SELECT id FROM ingestion.projects WHERE id = %s",
                (project_id,),
            )
            if await project.fetchone() is None:
                raise RegistryNotFoundError(f"project not found: {project_id}")
            try:
                cursor = await connection.execute(
                    """
                    INSERT INTO ingestion.documents(
                        id, project_id, title, document_type, content_profile
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, project_id, title, document_type,
                              content_profile, deletion_status, deletion_error,
                              deletion_requested_at, created_at
                    """,
                    (
                        uuid4(),
                        project_id,
                        payload.title,
                        payload.document_type.value,
                        payload.content_profile,
                    ),
                )
                row = await cursor.fetchone()
            except UniqueViolation as exc:
                raise RegistryConflictError(
                    f"document already exists in project: {payload.title}"
                ) from exc
        assert row is not None
        return DocumentResponse(**row)

    async def list_documents(self, project_id: UUID) -> list[DocumentResponse]:
        async with self._pool.connection() as connection:
            project = await connection.execute(
                "SELECT id FROM ingestion.projects WHERE id = %s",
                (project_id,),
            )
            if await project.fetchone() is None:
                raise RegistryNotFoundError(f"project not found: {project_id}")
            cursor = await connection.execute(
                """
                SELECT id, project_id, title, document_type,
                       content_profile, deletion_status, deletion_error,
                       deletion_requested_at, created_at
                FROM ingestion.documents
                WHERE project_id = %s
                ORDER BY lower(title), id
                """,
                (project_id,),
            )
            rows = await cursor.fetchall()
        return [DocumentResponse(**row) for row in rows]

    async def document_project_id(self, document_id: UUID) -> UUID:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT project_id FROM ingestion.documents WHERE id = %s",
                (document_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryNotFoundError(f"document not found: {document_id}")
        return row["project_id"]

    async def create_version(
        self,
        *,
        version_id: UUID,
        document_id: UUID,
        version_label: str,
        file_path: str,
        content_hash: str,
        source_filename: str,
        content_profile: str,
        retrieval_metadata: dict,
    ) -> DocumentVersionResponse:
        run_id = uuid4()
        async with self._pool.connection() as connection:
            try:
                async with connection.transaction():
                    document = await connection.execute(
                        """
                        SELECT id, deletion_status
                        FROM ingestion.documents
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (document_id,),
                    )
                    document_row = await document.fetchone()
                    if document_row is None:
                        raise RegistryNotFoundError(
                            f"document not found: {document_id}"
                        )
                    if document_row["deletion_status"] != "ACTIVE":
                        raise RegistryConflictError(
                            "document deletion is in progress or requires retry"
                        )
                    duplicate = await connection.execute(
                        """
                        SELECT version_label, content_hash
                        FROM ingestion.document_versions
                        WHERE document_id = %s
                          AND (version_label = %s OR content_hash = %s)
                        """,
                        (document_id, version_label, content_hash),
                    )
                    existing = await duplicate.fetchone()
                    if existing is not None:
                        if existing["content_hash"] == content_hash:
                            raise RegistryConflictError(
                                "this file content is already registered for the document"
                            )
                        raise RegistryConflictError(
                            f"version label already exists: {version_label}"
                        )

                    await connection.execute(
                        """
                        INSERT INTO ingestion.document_versions(
                            id,
                            document_id,
                            version_label,
                            status,
                            file_path,
                            content_hash,
                            source_filename,
                            content_profile,
                            retrieval_metadata
                        )
                        VALUES (%s, %s, %s, 'DRAFT', %s, %s, %s, %s, %s)
                        """,
                        (
                            version_id,
                            document_id,
                            version_label,
                            file_path,
                            content_hash,
                            source_filename,
                            content_profile,
                            Jsonb(retrieval_metadata),
                        ),
                    )
                    await connection.execute(
                        """
                        INSERT INTO ingestion.ingestion_runs(
                            id,
                            document_version_id,
                            processing_status,
                            chunk_count
                        )
                        VALUES (%s, %s, 'QUEUED', 0)
                        """,
                        (run_id, version_id),
                    )
            except UniqueViolation as exc:
                raise RegistryConflictError(
                    "version label or file content is already registered"
                ) from exc
        return await self.get_version(version_id)

    async def extraction_source(self, version_id: UUID) -> dict:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    version.id,
                    version.file_path,
                    version.source_filename,
                    version.content_hash,
                    version.retrieval_metadata,
                    version.llm_metadata,
                    document.document_type,
                    version.content_profile
                FROM ingestion.document_versions AS version
                JOIN ingestion.documents AS document
                  ON document.id = version.document_id
                WHERE version.id = %s
                  AND document.deletion_status = 'ACTIVE'
                """,
                (version_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryNotFoundError(f"version not found: {version_id}")
        return row

    async def recover_incomplete_extractions(self) -> list[UUID]:
        """Requeue work interrupted by a service restart and return queued versions."""
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE ingestion.ingestion_runs
                SET processing_status = 'QUEUED', started_at = NULL
                WHERE processing_status = 'RUNNING'
                  AND document_version_id IN (
                      SELECT version.id
                      FROM ingestion.document_versions AS version
                      JOIN ingestion.documents AS document
                        ON document.id = version.document_id
                      WHERE document.deletion_status = 'ACTIVE'
                  )
                """
            )
            await connection.execute(
                """
                UPDATE ingestion.ingestion_runs
                SET processing_status = 'FAILED',
                    error_code = 'DOCUMENT_DELETING',
                    error = 'document deletion was requested',
                    completed_at = now()
                WHERE processing_status IN ('QUEUED', 'RUNNING')
                  AND document_version_id IN (
                      SELECT version.id
                      FROM ingestion.document_versions AS version
                      JOIN ingestion.documents AS document
                        ON document.id = version.document_id
                      WHERE document.deletion_status != 'ACTIVE'
                  )
                """
            )
            cursor = await connection.execute(
                """
                SELECT DISTINCT ON (document_version_id) document_version_id
                FROM ingestion.ingestion_runs AS run
                WHERE processing_status = 'QUEUED'
                  AND EXISTS (
                      SELECT 1
                      FROM ingestion.document_versions AS version
                      JOIN ingestion.documents AS document
                        ON document.id = version.document_id
                      WHERE version.id = run.document_version_id
                        AND document.deletion_status = 'ACTIVE'
                  )
                ORDER BY document_version_id, created_at DESC
                """
            )
            rows = await cursor.fetchall()
        return [row["document_version_id"] for row in rows]

    async def queue_extraction(
        self, version_id: UUID, *, content_hash: str | None = None
    ) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                version_cursor = await connection.execute(
                    """
                    SELECT version.id, version.document_id, version.status,
                           document.deletion_status
                    FROM ingestion.document_versions AS version
                    JOIN ingestion.documents AS document
                      ON document.id = version.document_id
                    WHERE version.id = %s
                    FOR UPDATE OF version, document
                    """,
                    (version_id,),
                )
                version = await version_cursor.fetchone()
                if version is None:
                    raise RegistryNotFoundError(f"version not found: {version_id}")
                if version["deletion_status"] != "ACTIVE":
                    raise RegistryConflictError("document deletion is in progress")
                if content_hash is not None:
                    if version["status"] != "DRAFT":
                        raise InvalidLifecycleStateError(
                            "only a DRAFT version can be reingested"
                        )
                    duplicate_cursor = await connection.execute(
                        """
                        SELECT 1
                        FROM ingestion.document_versions
                        WHERE document_id = %s
                          AND content_hash = %s
                          AND id != %s
                        """,
                        (version["document_id"], content_hash, version_id),
                    )
                    if await duplicate_cursor.fetchone() is not None:
                        raise RegistryConflictError(
                            "the edited source matches another registered version"
                        )
                active_cursor = await connection.execute(
                    """
                    SELECT 1
                    FROM ingestion.ingestion_runs
                    WHERE document_version_id = %s
                      AND processing_status IN ('QUEUED', 'RUNNING')
                    """,
                    (version_id,),
                )
                if await active_cursor.fetchone() is not None:
                    raise RegistryConflictError(
                        "an extraction run is already queued or running"
                    )
                if content_hash is not None:
                    await connection.execute(
                        """
                        UPDATE ingestion.document_versions
                        SET content_hash = %s
                        WHERE id = %s
                        """,
                        (content_hash, version_id),
                    )
                run_id = uuid4()
                await connection.execute(
                    """
                    INSERT INTO ingestion.ingestion_runs(
                        id, document_version_id, processing_status, chunk_count
                    )
                    VALUES (%s, %s, 'QUEUED', 0)
                    """,
                    (run_id, version_id),
                )
        return await self.get_extraction_run(run_id)

    async def start_extraction(
        self,
        version_id: UUID,
        extractor_version: str,
        *,
        renderer_version: str | None = None,
        vision_provider: str | None = None,
        vision_model: str | None = None,
        vision_prompt_version: str | None = None,
    ) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE ingestion.ingestion_runs
                SET
                    processing_status = 'RUNNING',
                    extractor_version = %s,
                    renderer_version = %s,
                    vision_provider = %s,
                    vision_model = %s,
                    vision_prompt_version = %s,
                    started_at = now(),
                    completed_at = NULL,
                    error = NULL,
                    error_code = NULL
                WHERE id = (
                    SELECT id
                    FROM ingestion.ingestion_runs
                    WHERE document_version_id = %s
                      AND processing_status = 'QUEUED'
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                RETURNING id
                """,
                (
                    extractor_version,
                    renderer_version,
                    vision_provider,
                    vision_model,
                    vision_prompt_version,
                    version_id,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryConflictError("no queued extraction run exists")
        return await self.get_extraction_run(row["id"])

    async def complete_extraction(
        self,
        run_id: UUID,
        *,
        source_format: str,
        normalized_path: str,
        quality_report: dict,
    ) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE ingestion.ingestion_runs
                SET
                    processing_status = 'READY',
                    source_format = %s,
                    normalized_path = %s,
                    quality_report = %s,
                    completed_at = now(),
                    error = NULL,
                    error_code = NULL
                WHERE id = %s AND processing_status = 'RUNNING'
                RETURNING id
                """,
                (
                    source_format,
                    normalized_path,
                    Jsonb(quality_report),
                    run_id,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryConflictError("extraction run is not running")
        return await self.get_extraction_run(run_id)

    async def fail_extraction(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error: str,
        source_format: str | None = None,
        normalized_path: str | None = None,
        quality_report: dict | None = None,
    ) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE ingestion.ingestion_runs
                SET
                    processing_status = 'FAILED',
                    error_code = %s,
                    error = %s,
                    source_format = %s,
                    normalized_path = %s,
                    quality_report = %s,
                    completed_at = now()
                WHERE id = %s AND processing_status = 'RUNNING'
                RETURNING id
                """,
                (
                    error_code,
                    error,
                    source_format,
                    normalized_path,
                    Jsonb(quality_report) if quality_report is not None else None,
                    run_id,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryConflictError("extraction run is not running")
        return await self.get_extraction_run(run_id)

    async def review_extraction(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error: str,
        source_format: str | None = None,
        normalized_path: str | None = None,
        quality_report: dict | None = None,
    ) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE ingestion.ingestion_runs
                SET
                    processing_status = 'NEEDS_REVIEW',
                    error_code = %s,
                    error = %s,
                    source_format = %s,
                    normalized_path = %s,
                    quality_report = %s,
                    completed_at = now()
                WHERE id = %s AND processing_status = 'RUNNING'
                RETURNING id
                """,
                (
                    error_code,
                    error,
                    source_format,
                    normalized_path,
                    Jsonb(quality_report) if quality_report is not None else None,
                    run_id,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryConflictError("extraction run is not running")
        return await self.get_extraction_run(run_id)

    async def get_extraction_run(self, run_id: UUID) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    document_version_id,
                    processing_status,
                    extractor_version,
                    renderer_version,
                    vision_provider,
                    vision_model,
                    vision_prompt_version,
                    source_format,
                    normalized_path,
                    quality_report,
                    error_code,
                    error,
                    started_at,
                    completed_at,
                    created_at
                FROM ingestion.ingestion_runs
                WHERE id = %s
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryNotFoundError(f"extraction run not found: {run_id}")
        return ExtractionRunResponse(**row)

    async def latest_extraction(
        self, version_id: UUID
    ) -> ExtractionRunResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    document_version_id,
                    processing_status,
                    extractor_version,
                    renderer_version,
                    vision_provider,
                    vision_model,
                    vision_prompt_version,
                    source_format,
                    normalized_path,
                    quality_report,
                    error_code,
                    error,
                    started_at,
                    completed_at,
                    created_at
                FROM ingestion.ingestion_runs
                WHERE document_version_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (version_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryNotFoundError(
                f"no extraction run exists for version: {version_id}"
            )
        return ExtractionRunResponse(**row)

    async def get_version(self, version_id: UUID) -> DocumentVersionResponse:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                VERSION_SELECT + " WHERE version.id = %s",
                (version_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RegistryNotFoundError(f"version not found: {version_id}")
        return DocumentVersionResponse(**row)

    async def list_versions(
        self, document_id: UUID
    ) -> list[DocumentVersionResponse]:
        async with self._pool.connection() as connection:
            document = await connection.execute(
                "SELECT id FROM ingestion.documents WHERE id = %s",
                (document_id,),
            )
            if await document.fetchone() is None:
                raise RegistryNotFoundError(f"document not found: {document_id}")
            cursor = await connection.execute(
                VERSION_SELECT
                + " WHERE version.document_id = %s"
                " ORDER BY version.created_at, version.id",
                (document_id,),
            )
            rows = await cursor.fetchall()
        return [DocumentVersionResponse(**row) for row in rows]

    async def approve_version(
        self, version_id: UUID, approved_by: str
    ) -> DocumentVersionResponse:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                target_cursor = await connection.execute(
                    """
                    SELECT version.id, version.document_id, version.status
                    FROM ingestion.document_versions AS version
                    JOIN ingestion.documents AS document
                      ON document.id = version.document_id
                    WHERE version.id = %s
                    FOR UPDATE OF document, version
                    """,
                    (version_id,),
                )
                target = await target_cursor.fetchone()
                if target is None:
                    raise RegistryNotFoundError(f"version not found: {version_id}")
                if target["status"] != "DRAFT":
                    raise InvalidLifecycleStateError(
                        "only a DRAFT version can be approved"
                    )

                ready_cursor = await connection.execute(
                    """
                    SELECT 1
                    FROM ingestion.ingestion_runs
                    WHERE id = (
                        SELECT id
                        FROM ingestion.ingestion_runs
                        WHERE document_version_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                      AND processing_status IN ('READY', 'NEEDS_REVIEW')
                      AND normalized_path IS NOT NULL
                    """,
                    (version_id,),
                )
                if await ready_cursor.fetchone() is None:
                    raise InvalidLifecycleStateError(
                        "version is not ready for approval"
                    )

                previous_cursor = await connection.execute(
                    """
                    SELECT id
                    FROM ingestion.document_versions
                    WHERE document_id = %s AND status = 'APPROVED'
                    FOR UPDATE
                    """,
                    (target["document_id"],),
                )
                previous = await previous_cursor.fetchone()
                previous_id = previous["id"] if previous is not None else None
                if previous_id is not None:
                    await connection.execute(
                        """
                        UPDATE ingestion.document_versions
                        SET status = 'ARCHIVED'
                        WHERE id = %s
                        """,
                        (previous_id,),
                    )

                await connection.execute(
                    """
                    UPDATE ingestion.document_versions
                    SET
                        status = 'APPROVED',
                        approved_at = now(),
                        approved_by = %s,
                        supersedes_version_id = %s
                    WHERE id = %s
                    """,
                    (approved_by, previous_id, version_id),
                )
                await connection.execute(
                    """
                    UPDATE ingestion.ingestion_runs
                    SET processing_status = 'READY'
                    WHERE id = (
                        SELECT id
                        FROM ingestion.ingestion_runs
                        WHERE document_version_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                      AND processing_status = 'NEEDS_REVIEW'
                    """,
                    (version_id,),
                )
        return await self.get_version(version_id)

    async def delete_draft(self, version_id: UUID) -> str:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT status, file_path
                    FROM ingestion.document_versions
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (version_id,),
                )
                version = await cursor.fetchone()
                if version is None:
                    raise RegistryNotFoundError(f"version not found: {version_id}")
                if version["status"] != "DRAFT":
                    raise InvalidLifecycleStateError(
                        "only a DRAFT version can be deleted"
                    )
                await connection.execute(
                    "DELETE FROM ingestion.document_versions WHERE id = %s",
                    (version_id,),
                )
        return version["file_path"]

    async def mark_document_deleting(
        self, document_id: UUID
    ) -> DocumentDeletionResponse:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT id, deletion_status
                    FROM ingestion.documents
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (document_id,),
                )
                document = await cursor.fetchone()
                if document is None:
                    raise RegistryNotFoundError(
                        f"document not found: {document_id}"
                    )
                count_cursor = await connection.execute(
                    """
                    SELECT count(*)::integer AS version_count
                    FROM ingestion.document_versions
                    WHERE document_id = %s
                    """,
                    (document_id,),
                )
                version_count = await count_cursor.fetchone()
                await connection.execute(
                    """
                    UPDATE ingestion.documents
                    SET deletion_status = 'DELETING',
                        deletion_error = NULL,
                        deletion_requested_at = now()
                    WHERE id = %s
                    """,
                    (document_id,),
                )
                await connection.execute(
                    """
                    UPDATE ingestion.ingestion_runs
                    SET processing_status = 'FAILED',
                        error_code = 'DOCUMENT_DELETING',
                        error = 'document deletion was requested',
                        completed_at = now()
                    WHERE processing_status = 'QUEUED'
                      AND document_version_id IN (
                          SELECT id
                          FROM ingestion.document_versions
                          WHERE document_id = %s
                      )
                    """,
                    (document_id,),
                )
        return DocumentDeletionResponse(
            document_id=document_id,
            status="DELETING",
            version_count=version_count["version_count"],
        )

    async def document_deletion_plan(self, document_id: UUID) -> dict:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, project_id, title, deletion_status
                FROM ingestion.documents
                WHERE id = %s
                """,
                (document_id,),
            )
            document = await cursor.fetchone()
            if document is None:
                raise RegistryNotFoundError(
                    f"document not found: {document_id}"
                )
            points_cursor = await connection.execute(
                """
                SELECT chunk.qdrant_point_id
                FROM ingestion.chunks AS chunk
                JOIN ingestion.document_versions AS version
                  ON version.id = chunk.document_version_id
                WHERE version.document_id = %s
                """,
                (document_id,),
            )
            point_rows = await points_cursor.fetchall()
            running_cursor = await connection.execute(
                """
                SELECT count(*)::integer AS running_count
                FROM ingestion.ingestion_runs AS run
                JOIN ingestion.document_versions AS version
                  ON version.id = run.document_version_id
                WHERE version.document_id = %s
                  AND run.processing_status = 'RUNNING'
                """,
                (document_id,),
            )
            running = await running_cursor.fetchone()
        return {
            **document,
            "qdrant_point_ids": [
                row["qdrant_point_id"] for row in point_rows
            ],
            "running_count": running["running_count"],
        }

    async def complete_document_deletion(
        self, document_id: UUID, deleted_by: str
    ) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT id, project_id, title, deletion_status
                    FROM ingestion.documents
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (document_id,),
                )
                document = await cursor.fetchone()
                if document is None:
                    return
                if document["deletion_status"] != "DELETING":
                    raise RegistryConflictError(
                        "document is not marked for deletion"
                    )
                count_cursor = await connection.execute(
                    """
                    SELECT count(*)::integer AS version_count
                    FROM ingestion.document_versions
                    WHERE document_id = %s
                    """,
                    (document_id,),
                )
                version_count = await count_cursor.fetchone()
                await connection.execute(
                    """
                    INSERT INTO ingestion.document_deletion_audit(
                        document_id, project_id, title, version_count, deleted_by
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO NOTHING
                    """,
                    (
                        document_id,
                        document["project_id"],
                        document["title"],
                        version_count["version_count"],
                        deleted_by,
                    ),
                )
                await connection.execute(
                    """
                    UPDATE ingestion.document_versions
                    SET supersedes_version_id = NULL
                    WHERE document_id = %s
                    """,
                    (document_id,),
                )
                await connection.execute(
                    "DELETE FROM ingestion.documents WHERE id = %s",
                    (document_id,),
                )

    async def fail_document_deletion(
        self, document_id: UUID, error: str
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE ingestion.documents
                SET deletion_status = 'DELETE_FAILED', deletion_error = %s
                WHERE id = %s
                """,
                (error[:2000], document_id),
            )

    async def pending_document_deletions(self) -> list[UUID]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id
                FROM ingestion.documents
                WHERE deletion_status = 'DELETING'
                ORDER BY deletion_requested_at, id
                """
            )
            rows = await cursor.fetchall()
        return [row["id"] for row in rows]
