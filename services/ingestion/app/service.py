from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import UploadFile

from .errors import RegistryConflictError, RegistryNotFoundError
from .extraction import ExtractionPipeline, NormalizedDocument
from .extraction.models import SlideHumanResolution
from .extraction.pptx_resolution import find_review_question, review_questions
from .extraction.errors import ExtractionFailure
from .extraction.pptx_report import build_pptx_llm_report
from .qdrant import QdrantProbe
from .repository import RegistryRepository
from .schemas import (
    CONTENT_PROFILE,
    DocumentDeletionResponse,
    DocumentVersionResponse,
    ExtractionRunResponse,
    ProjectDeletionResponse,
)
from .storage import VersionStorage
from .vision.base import SlideResolutionProvider, VisionProviderError


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
        resolution_provider: SlideResolutionProvider,
        resolution_prompt_version: str,
        qdrant: QdrantProbe,
        qdrant_collection: str,
    ) -> None:
        self.repository = repository
        self._storage = storage
        self._approved_by = approved_by
        self._extraction = extraction
        self._resolution_provider = resolution_provider
        self._resolution_prompt_version = resolution_prompt_version
        self._qdrant = qdrant
        self._qdrant_collection = qdrant_collection
        self._background_tasks: set[asyncio.Task] = set()
        self._deletion_tasks: dict[UUID, asyncio.Task] = {}
        self._project_deletion_tasks: dict[UUID, asyncio.Task] = {}
        self._review_locks: dict[UUID, asyncio.Lock] = {}

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
        for project_id in await self.repository.pending_project_deletions():
            self._schedule_project_deletion(project_id)
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

    async def review_report(self, version_id: UUID) -> str:
        version = await self.repository.get_version(version_id)
        document = await self.normalized_document(version_id)
        if document.source_format != "pptx":
            warnings = document.quality.warnings or [
                "No structured inconsistency questions were produced."
            ]
            return "\n".join(
                [
                    "# Document review report",
                    "",
                    f"Source: `{document.source_filename}`",
                    f"Format: `{document.source_format}`",
                    "",
                    "## Extraction warnings",
                    "",
                    *[f"- {warning}" for warning in warnings],
                    "",
                ]
            )
        try:
            return self._storage.read_pptx_llm_report(version.file_path)
        except FileNotFoundError as exc:
            raise RegistryConflictError(
                "the PPTX review report is not available yet"
            ) from exc

    async def review_artifact(
        self, version_id: UUID, artifact_path: str
    ) -> Path:
        version = await self.repository.get_version(version_id)
        try:
            return self._storage.review_artifact(
                version.file_path, artifact_path
            )
        except (FileNotFoundError, ValueError) as exc:
            raise RegistryNotFoundError("review artifact not found") from exc

    async def review_workflow(self, version_id: UUID) -> dict:
        version = await self.repository.get_version(version_id)
        document = await self.normalized_document(version_id)
        groups = []
        total = 0
        applied = 0
        for slide in document.slides:
            questions = review_questions(slide)
            if not questions:
                continue
            total += len(questions)
            applied += sum(
                1
                for question in questions
                if question["resolution"] is not None
                and question["resolution"]["status"] == "APPLIED"
            )
            groups.append(
                {
                    "locator": {
                        "kind": "slide",
                        "number": slide.slide_number,
                        "label": f"Slide {slide.slide_number}",
                    },
                    "title": slide.title or "Untitled",
                    "artifact_path": slide.image_path,
                    "questions": questions,
                }
            )
        return {
            "version_id": str(version_id),
            "source_filename": version.source_filename,
            "source_format": document.source_format,
            "processing_status": version.processing_status.value,
            "question_count": total,
            "applied_count": applied,
            "complete": total > 0 and applied == total,
            "groups": groups,
        }

    async def resolve_review_question(
        self, version_id: UUID, question_id: str, answer: str
    ) -> dict:
        cleaned_answer = answer.strip()
        if not cleaned_answer:
            raise RegistryConflictError("an answer is required")
        lock = self._review_locks.setdefault(version_id, asyncio.Lock())
        async with lock:
            version = await self.repository.get_version(version_id)
            document = await self.normalized_document(version_id)
            slide, question = self._locate_question(document, question_id)
            existing = question["resolution"]
            if existing is not None and existing["status"] == "APPLIED":
                raise RegistryConflictError(
                    "this resolution has already been applied"
                )
            assert slide.review is not None
            finding = slide.review.findings[question["finding_index"]]
            try:
                image_path = self._storage.review_artifact(
                    version.file_path, slide.image_path
                )
            except (FileNotFoundError, ValueError) as exc:
                raise RegistryConflictError(
                    "the rendered slide image is unavailable"
                ) from exc
            started = perf_counter()
            try:
                result = await asyncio.to_thread(
                    self._resolution_provider.resolve_slide,
                    image_path,
                    slide_number=slide.slide_number,
                    content_profile=document.content_profile
                    or "general_document",
                    native_markdown=slide.native_markdown,
                    current_markdown=self._current_review_markdown(slide),
                    finding=finding,
                    question=question["question"],
                    answer=cleaned_answer,
                    prompt_version=self._resolution_prompt_version,
                )
            except VisionProviderError as exc:
                raise RegistryConflictError(
                    f"the resolution model failed: {exc}"
                ) from exc
            duration_ms = max(0, round((perf_counter() - started) * 1000))
            response_hash = hashlib.sha256(
                result.model_dump_json().encode("utf-8")
            ).hexdigest()
            resolution = SlideHumanResolution(
                question_id=question_id,
                finding_index=question["finding_index"],
                question=question["question"],
                answer=cleaned_answer,
                resolved_markdown=result.resolved_markdown,
                rationale=result.rationale,
                remaining_uncertainty=result.remaining_uncertainty,
                follow_up_question=result.follow_up_question,
                provider=self._resolution_provider.name,
                model=self._resolution_provider.model,
                prompt_version=self._resolution_prompt_version,
                response_hash=response_hash,
                duration_ms=duration_ms,
            )
            updated_resolutions = [
                item
                for item in slide.human_resolutions
                if item.question_id != question_id
            ] + [resolution]
            updated_slide = slide.model_copy(
                update={"human_resolutions": updated_resolutions}
            )
            document = self._replace_slide(document, updated_slide)
            await asyncio.to_thread(
                self._persist_review_document, version.file_path, document
            )
        return await self.review_workflow(version_id)

    async def apply_review_resolution(
        self, version_id: UUID, question_id: str
    ) -> dict:
        lock = self._review_locks.setdefault(version_id, asyncio.Lock())
        async with lock:
            version = await self.repository.get_version(version_id)
            document = await self.normalized_document(version_id)
            slide, question = self._locate_question(document, question_id)
            current = next(
                (
                    item
                    for item in slide.human_resolutions
                    if item.question_id == question_id
                ),
                None,
            )
            if current is None:
                raise RegistryConflictError(
                    "resolve the question before applying it"
                )
            if current.remaining_uncertainty:
                raise RegistryConflictError(
                    "the model still needs clarification; answer the follow-up first"
                )
            applied = current.model_copy(update={"status": "APPLIED"})
            updated_resolutions = [
                applied if item.question_id == question_id else item
                for item in slide.human_resolutions
            ]
            updated_slide = slide.model_copy(
                update={
                    "human_resolutions": updated_resolutions,
                    # Proposals are appended in generation order and each is
                    # produced from the latest prior proposal. Keep the newest
                    # cumulative proposal when applying the batch, regardless
                    # of question display order.
                    "combined_markdown": updated_resolutions[-1].resolved_markdown,
                }
            )
            document = self._replace_slide(document, updated_slide)
            complete = self._all_questions_applied(document)
            await asyncio.to_thread(
                self._persist_review_document, version.file_path, document
            )
            if complete:
                await self.repository.complete_human_review(version_id)
        return await self.review_workflow(version_id)

    @staticmethod
    def _locate_question(document: NormalizedDocument, question_id: str):
        for slide in document.slides:
            try:
                return slide, find_review_question(slide, question_id)
            except KeyError:
                continue
        raise RegistryNotFoundError(f"review question not found: {question_id}")

    @staticmethod
    def _replace_slide(document: NormalizedDocument, updated_slide):
        return document.model_copy(
            update={
                "slides": [
                    updated_slide
                    if slide.slide_number == updated_slide.slide_number
                    else slide
                    for slide in document.slides
                ]
            }
        )

    @staticmethod
    def _current_review_markdown(slide) -> str:
        proposed = [
            resolution.resolved_markdown
            for resolution in slide.human_resolutions
            if resolution.status == "PROPOSED"
        ]
        return proposed[-1] if proposed else slide.combined_markdown

    @staticmethod
    def _all_questions_applied(document: NormalizedDocument) -> bool:
        questions = [
            question
            for slide in document.slides
            for question in review_questions(slide)
        ]
        return bool(questions) and all(
            question["resolution"] is not None
            and question["resolution"]["status"] == "APPLIED"
            for question in questions
        )

    def _persist_review_document(
        self, source_path: str, document: NormalizedDocument
    ) -> None:
        self._storage.write_normalized(
            source_path, document.model_dump(mode="json")
        )
        self._write_pptx_llm_report(source_path, document)

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

    async def delete_project(
        self, project_id: UUID
    ) -> ProjectDeletionResponse:
        result = await self.repository.mark_project_deleting(project_id)
        self._schedule_project_deletion(project_id)
        return result

    def _schedule_project_deletion(self, project_id: UUID) -> None:
        existing = self._project_deletion_tasks.get(project_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._purge_project(project_id))
        self._project_deletion_tasks[project_id] = task

        def cleanup(completed: asyncio.Task) -> None:
            self._project_deletion_tasks.pop(project_id, None)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.error(
                    "project deletion task failed unexpectedly for %s: %s",
                    project_id,
                    error,
                )

        task.add_done_callback(cleanup)

    async def _purge_project(self, project_id: UUID) -> None:
        try:
            plan = await self.repository.project_deletion_plan(project_id)
            for document_id in plan["document_ids"]:
                existing = self._deletion_tasks.get(document_id)
                if existing is None or existing.done():
                    self._schedule_document_deletion(document_id)
                    existing = self._deletion_tasks[document_id]
                await existing

            final_plan = await self.repository.project_deletion_plan(project_id)
            if final_plan["document_ids"]:
                raise RegistryConflictError(
                    "one or more project documents could not be deleted"
                )
            await self._storage.delete_project(project_id)
            await self.repository.complete_project_deletion(project_id)
            logger.info("project and all linked data deleted: %s", project_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("project deletion failed for %s", project_id)
            await self.repository.fail_project_deletion(
                project_id,
                f"{type(exc).__name__}: {exc}",
            )

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
