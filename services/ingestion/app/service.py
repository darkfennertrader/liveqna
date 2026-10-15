from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile

from .errors import RegistryConflictError
from .extraction import ExtractionPipeline, NormalizedDocument
from .extraction.errors import ExtractionFailure
from .extraction.pptx_report import build_pptx_llm_report
from .qdrant import QdrantProbe
from .repository import RegistryRepository
from .schemas import (
    CONTENT_PROFILE,
    DocumentDeletionResponse,
    DocumentVersionResponse,
    ExtractionRunResponse,
)
from .storage import VersionStorage


logger = logging.getLogger("ingestion.service")


REVIEW_REQUIRED_CODES = {
    "OCR_REQUIRED",
    "PDF_REVIEW_REQUIRED",
    "PPTX_REVIEW_REQUIRED",
}


class RegistryService:
    def __init__(
        self,
        repository: RegistryRepository,
        storage: VersionStorage,
        approved_by: str,
        extraction: ExtractionPipeline,
        qdrant: QdrantProbe,
        qdrant_collection: str,
    ) -> None:
        self.repository = repository
        self._storage = storage
        self._approved_by = approved_by
        self._extraction = extraction
        self._qdrant = qdrant
        self._qdrant_collection = qdrant_collection
        self._background_tasks: set[asyncio.Task] = set()
        self._deletion_tasks: dict[UUID, asyncio.Task] = {}

    @property
    def active_extraction_count(self) -> int:
        return len(self._background_tasks)

    async def create_version(
        self,
        document_id: UUID,
        version_label: str,
        upload: UploadFile,
        content_profile: str,
        retrieval_metadata: dict,
    ) -> DocumentVersionResponse:
        cleaned_label = version_label.strip()
        if not cleaned_label:
            raise RegistryConflictError("version label must not be empty")
        if len(cleaned_label) > 100:
            raise RegistryConflictError(
                "version label must be 100 characters or fewer"
            )
        cleaned_profile = content_profile.strip().lower()
        if CONTENT_PROFILE.fullmatch(cleaned_profile) is None:
            raise RegistryConflictError(
                "content profile must use lower-case letters, numbers, and underscores"
            )
        source_filename = (upload.filename or "").strip()
        if not source_filename:
            raise RegistryConflictError("uploaded file must have a filename")

        project_id = await self.repository.document_project_id(document_id)
        version_id = uuid4()
        stored = await self._storage.save_upload(
            project_id, document_id, version_id, upload
        )
        try:
            version = await self.repository.create_version(
                version_id=version_id,
                document_id=document_id,
                version_label=cleaned_label,
                file_path=stored.relative_path,
                content_hash=stored.content_hash,
                source_filename=source_filename,
                content_profile=cleaned_profile,
                retrieval_metadata=retrieval_metadata,
            )
        except Exception:
            await self._storage.delete_version(stored.relative_path)
            raise
        self.schedule_extraction(version.id)
        return version

    async def extract_version(self, version_id: UUID) -> ExtractionRunResponse:
        version = await self.repository.get_version(version_id)
        if not self._storage.exists(version.file_path):
            raise RegistryConflictError(
                "stored source file is missing; reingestion cannot start"
            )
        content_hash = await asyncio.to_thread(
            self._storage.content_hash, version.file_path
        )
        run = await self.repository.queue_extraction(
            version_id, content_hash=content_hash
        )
        self.schedule_extraction(version_id)
        return run

    def schedule_extraction(self, version_id: UUID) -> None:
        task = asyncio.create_task(self._run_extraction(version_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._extraction_finished)

    def _extraction_finished(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "background extraction task failed before status could be recorded: %s",
                error,
            )

    async def recover_incomplete_extractions(self) -> None:
        for version_id in await self.repository.recover_incomplete_extractions():
            self.schedule_extraction(version_id)

    async def recover_pending_deletions(self) -> None:
        for document_id in await self.repository.pending_document_deletions():
            self._schedule_document_deletion(document_id)

    async def latest_extraction(
        self, version_id: UUID
    ) -> ExtractionRunResponse:
        return await self.repository.latest_extraction(version_id)

    async def normalized_document(self, version_id: UUID) -> NormalizedDocument:
        run = await self.repository.latest_extraction(version_id)
        if run.normalized_path is None:
            raise RegistryConflictError(
                "the latest extraction run has no normalized output"
            )
        try:
            payload = self._storage.read_normalized(run.normalized_path)
        except FileNotFoundError as exc:
            raise RegistryConflictError(
                "normalized output is missing from local storage"
            ) from exc
        return NormalizedDocument.model_validate(payload)

    async def _run_extraction(self, version_id: UUID) -> ExtractionRunResponse:
        source = await self.repository.extraction_source(version_id)
        suffix = Path(source["source_filename"]).suffix.lower()
        effective_document_type = (
            "transcript"
            if source["content_profile"] == "transcript"
            else source["document_type"]
        )
        is_pptx = suffix == ".pptx"
        is_general_pdf = (
            suffix == ".pdf" and effective_document_type != "transcript"
        )
        renderer_version = None
        vision_provider = None
        vision_model = None
        vision_prompt_version = None
        if is_pptx or is_general_pdf:
            renderer = (
                self._extraction.pptx_renderer
                if is_pptx
                else self._extraction.pdf_renderer
            )
            if renderer is not None:
                renderer_version = renderer.version
            if self._extraction.vision_provider is not None:
                vision_provider = self._extraction.vision_provider.name
                vision_model = self._extraction.vision_provider.model
            vision_prompt_version = (
                self._extraction.vision_prompt_version
                if is_pptx
                else self._extraction.pdf_vision_prompt_version
            )
        run = await self.repository.start_extraction(
            version_id,
            self._extraction.version,
            renderer_version=renderer_version,
            vision_provider=vision_provider,
            vision_model=vision_model,
            vision_prompt_version=vision_prompt_version,
        )
        try:
            normalized = await asyncio.to_thread(
                self._extraction.extract,
                self._storage.absolute_path(source["file_path"]),
                source["source_filename"],
                effective_document_type,
                source["content_hash"],
                source["content_profile"],
            )
            normalized = normalized.model_copy(
                update={
                    "retrieval_metadata": source["retrieval_metadata"],
                    "llm_metadata": source["llm_metadata"],
                    "content_profile": source["content_profile"],
                }
            )
            await asyncio.to_thread(
                self._write_pptx_llm_report, source["file_path"], normalized
            )
            normalized_path = await asyncio.to_thread(
                self._storage.write_normalized,
                source["file_path"],
                normalized.model_dump(mode="json"),
            )
            return await self.repository.complete_extraction(
                run.id,
                source_format=normalized.source_format,
                normalized_path=normalized_path,
                quality_report=normalized.quality.model_dump(mode="json"),
            )
        except ExtractionFailure as exc:
            normalized_path = None
            source_format = None
            quality_report = None
            if exc.partial_document is not None:
                partial_document = exc.partial_document.model_copy(
                    update={
                        "retrieval_metadata": source["retrieval_metadata"],
                        "llm_metadata": source["llm_metadata"],
                        "content_profile": source["content_profile"],
                    }
                )
                source_format = exc.partial_document.source_format
                quality_report = exc.partial_document.quality.model_dump(
                    mode="json"
                )
                await asyncio.to_thread(
                    self._write_pptx_llm_report,
                    source["file_path"],
                    partial_document,
                )
                normalized_path = await asyncio.to_thread(
                    self._storage.write_normalized,
                    source["file_path"],
                    partial_document.model_dump(mode="json"),
                )
            operation = (
                self.repository.review_extraction
                if exc.code in REVIEW_REQUIRED_CODES
                else self.repository.fail_extraction
            )
            return await operation(
                run.id,
                error_code=exc.code,
                error=str(exc),
                source_format=source_format,
                normalized_path=normalized_path,
                quality_report=quality_report,
            )
        except Exception as exc:
            logger.exception("unexpected extraction failure for %s", version_id)
            return await self.repository.fail_extraction(
                run.id,
                error_code="EXTRACTION_FAILED",
                error=f"unexpected extraction failure: {type(exc).__name__}",
            )

    def _write_pptx_llm_report(
        self, source_path: str, normalized: NormalizedDocument
    ) -> None:
        if normalized.source_format != "pptx" or not normalized.slides:
            return
        report = build_pptx_llm_report(normalized)
        self._storage.write_pptx_llm_report(source_path, report)

    async def approve_version(self, version_id: UUID) -> DocumentVersionResponse:
        version = await self.repository.get_version(version_id)
        if not self._storage.exists(version.file_path):
            raise RegistryConflictError(
                "stored file is missing; version cannot be approved"
            )
        return await self.repository.approve_version(
            version_id, self._approved_by
        )

    async def delete_draft(self, version_id: UUID) -> None:
        file_path = await self.repository.delete_draft(version_id)
        await self._storage.delete_version(file_path)

    async def delete_document(
        self, document_id: UUID
    ) -> DocumentDeletionResponse:
        result = await self.repository.mark_document_deleting(document_id)
        self._schedule_document_deletion(document_id)
        return result

    def _schedule_document_deletion(self, document_id: UUID) -> None:
        existing = self._deletion_tasks.get(document_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._purge_document(document_id))
        self._deletion_tasks[document_id] = task

        def cleanup(completed: asyncio.Task) -> None:
            self._deletion_tasks.pop(document_id, None)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.error(
                    "document deletion task failed unexpectedly for %s: %s",
                    document_id,
                    error,
                )

        task.add_done_callback(cleanup)

    async def _purge_document(self, document_id: UUID) -> None:
        try:
            plan = await self.repository.document_deletion_plan(document_id)
            await self._qdrant.delete_document_points(
                self._qdrant_collection,
                document_id,
                plan["qdrant_point_ids"],
            )
            while True:
                plan = await self.repository.document_deletion_plan(document_id)
                if plan["deletion_status"] != "DELETING":
                    return
                if plan["running_count"] == 0:
                    break
                await asyncio.sleep(0.25)

            # Sweep again after active work finishes in case it published
            # points between the deletion request and its final status update.
            plan = await self.repository.document_deletion_plan(document_id)
            await self._qdrant.delete_document_points(
                self._qdrant_collection,
                document_id,
                plan["qdrant_point_ids"],
            )
            await self._storage.delete_document(
                plan["project_id"], document_id
            )
            await self.repository.complete_document_deletion(
                document_id, self._approved_by
            )
            logger.info("document and all versions deleted: %s", document_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("document deletion failed for %s", document_id)
            await self.repository.fail_document_deletion(
                document_id,
                f"{type(exc).__name__}: {exc}",
            )
