from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .database import Database
from .errors import RegistryConflictError, RegistryNotFoundError
from .extraction import ExtractionPipeline, NormalizedDocument
from .extraction.docx_renderer import LibreOfficeDocxRenderer
from .extraction.docx_validation import DocxPackageLimits
from .extraction.pdf_renderer import PyMuPdfRenderer
from .extraction.pptx_renderer import LibreOfficePptxRenderer
from .extraction.text import TextExtractionLimits
from .extraction.xlsx_renderer import LibreOfficeXlsxRenderer
from .extraction.xlsx_validation import XlsxPackageLimits
from .qdrant import QdrantProbe
from .repository import RegistryRepository
from .review_cache import clear_review_runs_cache
from .schemas import (
    DeleteDraftResponse,
    DocumentCreate,
    DocumentDeletionResponse,
    DocumentResponse,
    DocumentVersionResponse,
    ExtractionRunResponse,
    ProjectCreate,
    ProjectDeletionResponse,
    ProjectResponse,
    ReviewAnswerRequest,
)
from .service import RegistryService
from .settings import Settings
from .storage import VersionStorage
from .vision import (
    create_resolution_provider,
    create_review_provider,
    create_vision_provider,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion.main")


async def _dependency_status(check) -> dict[str, Any]:
    try:
        ready = await check()
    except Exception as exc:  # health response must report, not mask, failures
        return {"status": "unavailable", "error": type(exc).__name__}
    return {"status": "ok" if ready else "unavailable"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_environment()
    settings.prepare_directories()
    database = Database(settings)
    qdrant = QdrantProbe(
        settings.qdrant_url, settings.dependency_timeout_seconds
    )
    await database.open()
    app.state.settings = settings
    app.state.database = database
    app.state.qdrant = qdrant
    app.state.registry = RegistryService(
        RegistryRepository(database.pool),
        VersionStorage(settings.document_storage_dir),
        settings.approved_by,
        ExtractionPipeline(
            pdf_renderer=PyMuPdfRenderer(dpi=settings.pdf_render_dpi),
            docx_renderer=LibreOfficeDocxRenderer(
                soffice_binary=settings.docx_soffice_binary,
                dpi=settings.docx_render_dpi,
                timeout_seconds=settings.docx_render_timeout_seconds,
            ),
            pptx_renderer=LibreOfficePptxRenderer(
                soffice_binary=settings.pptx_soffice_binary,
                pdftoppm_binary=settings.pptx_pdftoppm_binary,
                dpi=settings.pptx_render_dpi,
                timeout_seconds=settings.pptx_render_timeout_seconds,
            ),
            xlsx_renderer=LibreOfficeXlsxRenderer(
                soffice_binary=settings.xlsx_soffice_binary,
                uno_python=settings.xlsx_uno_python,
                helper_script=settings.xlsx_helper_script,
                dpi=settings.xlsx_render_dpi,
                timeout_seconds=settings.xlsx_render_timeout_seconds,
                max_pages_per_sheet=settings.xlsx_max_render_pages_per_sheet,
            ),
            vision_provider=create_vision_provider(settings),
            review_provider=create_review_provider(settings),
            pdf_vision_prompt_version=settings.pdf_vision_prompt_version,
            pdf_review_prompt_version=settings.pdf_review_prompt_version,
            docx_vision_prompt_version=settings.docx_vision_prompt_version,
            docx_review_prompt_version=settings.docx_review_prompt_version,
            vision_prompt_version=settings.vision_prompt_version,
            review_prompt_version=settings.review_prompt_version,
            xlsx_vision_prompt_version=settings.xlsx_vision_prompt_version,
            xlsx_review_prompt_version=settings.xlsx_review_prompt_version,
            xlsx_package_limits=XlsxPackageLimits(
                max_entries=settings.xlsx_max_package_entries,
                max_uncompressed_bytes=settings.xlsx_max_uncompressed_bytes,
                max_compression_ratio=settings.xlsx_max_compression_ratio,
                max_sheets=settings.xlsx_max_sheets,
                max_cells=settings.xlsx_max_cells,
            ),
            docx_package_limits=DocxPackageLimits(
                max_entries=settings.docx_max_package_entries,
                max_compressed_bytes=settings.docx_max_compressed_bytes,
                max_uncompressed_bytes=settings.docx_max_uncompressed_bytes,
                max_compression_ratio=settings.docx_max_compression_ratio,
                max_paragraphs=settings.docx_max_paragraphs,
                max_tables=settings.docx_max_tables,
                max_pages=settings.docx_max_pages,
            ),
            text_limits=TextExtractionLimits(
                max_bytes=settings.text_max_bytes,
                max_lines=settings.text_max_lines,
                max_line_characters=settings.text_max_line_characters,
                max_blocks=settings.text_max_blocks,
                max_block_characters=settings.text_max_block_characters,
            ),
            docx_revision_policy=settings.docx_revision_policy,
            xlsx_visual_mode=settings.xlsx_visual_mode,
            vision_max_concurrency=settings.vision_max_concurrency,
        ),
        create_resolution_provider(settings),
        settings.resolver_prompt_version,
        settings.pdf_resolver_prompt_version,
        settings.docx_resolver_prompt_version,
        settings.xlsx_resolver_prompt_version,
        qdrant,
        settings.qdrant_collection,
    )
    await app.state.registry.recover_pending_deletions()
    await app.state.registry.recover_incomplete_extractions()
    logger.info(
        "ingestion service ready; applied migrations=%s",
        database.applied_migrations,
    )
    try:
        yield
    finally:
        await qdrant.close()
        await database.close()


app = FastAPI(
    title="liveqna-ingestion",
    version="0.3.0-phase2",
    lifespan=lifespan,
)


@app.exception_handler(RegistryNotFoundError)
async def registry_not_found(
    _request: Request, exc: RegistryNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RegistryConflictError)
async def registry_conflict(
    _request: Request, exc: RegistryConflictError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def _registry(request: Request) -> RegistryService:
    return request.app.state.registry


@app.get("/runtime/status")
async def runtime_status(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    local_models = sorted(
        {
            model
            for provider, model in (
                (settings.vision_provider, settings.vision_model),
                (settings.review_provider, settings.review_model),
                (settings.resolver_provider, settings.resolver_model),
            )
            if provider == "ollama"
        }
    )
    return {
        "active_extraction_count": _registry(request).active_extraction_count,
        "local_models": local_models,
        "uses_local_gpu": bool(local_models),
    }


@app.post("/runtime/unload-models")
async def unload_runtime_models(request: Request) -> dict:
    registry = _registry(request)
    settings: Settings = request.app.state.settings
    model_names = sorted(
        {
            model
            for provider, model in (
                (settings.vision_provider, settings.vision_model),
                (settings.review_provider, settings.review_model),
                (settings.resolver_provider, settings.resolver_model),
            )
            if provider == "ollama"
        }
    )
    if model_names and registry.active_extraction_count:
        raise HTTPException(
            status_code=409,
            detail="local-model document ingestion is currently running",
        )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for model_name in model_names:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={"model": model_name, "prompt": "", "keep_alive": 0},
                )
                response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"failed to unload local ingestion model: {exc}",
        ) from exc
    return {"status": "unloaded", "models": model_names}


@app.delete("/review-runs-cache")
async def clear_review_runs(request: Request) -> dict:
    registry = _registry(request)
    if registry.active_extraction_count:
        raise HTTPException(
            status_code=409,
            detail="wait for document ingestion to finish before clearing Gate 2 runs",
        )
    settings: Settings = request.app.state.settings
    removed = await asyncio.to_thread(
        clear_review_runs_cache, settings.review_runs_cache_dir
    )
    return {"status": "cleared", "entries_removed": removed}


@app.get("/")
async def service_info() -> dict[str, str]:
    return {
        "service": "liveqna-ingestion",
        "phase": "2",
        "status": "document-extraction",
    }


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    database, qdrant = await asyncio.gather(
        _dependency_status(request.app.state.database.ping),
        _dependency_status(request.app.state.qdrant.ready),
    )
    healthy = database["status"] == "ok" and qdrant["status"] == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "phase": "2",
            "dependencies": {
                "postgres": database,
                "qdrant": qdrant,
            },
        },
    )


@app.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectCreate, request: Request
) -> ProjectResponse:
    return await _registry(request).repository.create_project(payload)


@app.get("/projects", response_model=list[ProjectResponse])
async def list_projects(request: Request) -> list[ProjectResponse]:
    return await _registry(request).repository.list_projects()


@app.delete(
    "/projects/{project_id}",
    response_model=ProjectDeletionResponse,
    status_code=202,
)
async def delete_project(
    project_id: UUID, request: Request
) -> ProjectDeletionResponse:
    return await _registry(request).delete_project(project_id)


@app.post(
    "/projects/{project_id}/documents",
    response_model=DocumentResponse,
    status_code=201,
)
async def create_document(
    project_id: UUID, payload: DocumentCreate, request: Request
) -> DocumentResponse:
    return await _registry(request).repository.create_document(
        project_id, payload
    )


@app.get(
    "/projects/{project_id}/documents",
    response_model=list[DocumentResponse],
)
async def list_documents(
    project_id: UUID, request: Request
) -> list[DocumentResponse]:
    return await _registry(request).repository.list_documents(project_id)


@app.delete(
    "/documents/{document_id}",
    response_model=DocumentDeletionResponse,
    status_code=202,
)
async def delete_document(
    document_id: UUID, request: Request
) -> DocumentDeletionResponse:
    return await _registry(request).delete_document(document_id)


@app.post(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=201,
)
async def create_version(
    document_id: UUID,
    request: Request,
    version_label: str = Form(...),
    content_profile: str = Form("general_document"),
    retrieval_metadata: str = Form("{}"),
    file: UploadFile = File(...),
) -> DocumentVersionResponse:
    try:
        parsed_metadata = json.loads(retrieval_metadata)
    except json.JSONDecodeError as exc:
        raise RegistryConflictError("retrieval metadata must be valid JSON") from exc
    if not isinstance(parsed_metadata, dict):
        raise RegistryConflictError("retrieval metadata must be a JSON object")
    try:
        return await _registry(request).create_version(
            document_id,
            version_label,
            file,
            content_profile,
            parsed_metadata,
        )
    finally:
        await file.close()


@app.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionResponse],
)
async def list_versions(
    document_id: UUID, request: Request
) -> list[DocumentVersionResponse]:
    return await _registry(request).repository.list_versions(document_id)


@app.get("/versions/{version_id}", response_model=DocumentVersionResponse)
async def get_version(
    version_id: UUID, request: Request
) -> DocumentVersionResponse:
    return await _registry(request).repository.get_version(version_id)


@app.get(
    "/versions/{version_id}/extraction",
    response_model=ExtractionRunResponse,
)
async def get_extraction(
    version_id: UUID, request: Request
) -> ExtractionRunResponse:
    return await _registry(request).latest_extraction(version_id)


@app.post(
    "/versions/{version_id}/extract",
    response_model=ExtractionRunResponse,
)
async def extract_version(
    version_id: UUID, request: Request
) -> ExtractionRunResponse:
    return await _registry(request).extract_version(version_id)


@app.get(
    "/versions/{version_id}/normalized",
    response_model=NormalizedDocument,
)
async def get_normalized_document(
    version_id: UUID, request: Request
) -> NormalizedDocument:
    return await _registry(request).normalized_document(version_id)


@app.get(
    "/versions/{version_id}/review-report",
    response_class=PlainTextResponse,
)
async def get_review_report(
    version_id: UUID, request: Request
) -> PlainTextResponse:
    report = await _registry(request).review_report(version_id)
    return PlainTextResponse(report, media_type="text/markdown; charset=utf-8")


@app.get("/versions/{version_id}/review-workflow")
async def get_review_workflow(version_id: UUID, request: Request) -> dict:
    return await _registry(request).review_workflow(version_id)


@app.post(
    "/versions/{version_id}/review-questions/{question_id}/resolve"
)
async def resolve_review_question(
    version_id: UUID,
    question_id: str,
    payload: ReviewAnswerRequest,
    request: Request,
) -> dict:
    return await _registry(request).resolve_review_question(
        version_id, question_id, payload.answer
    )


@app.post(
    "/versions/{version_id}/review-questions/{question_id}/apply"
)
async def apply_review_resolution(
    version_id: UUID, question_id: str, request: Request
) -> dict:
    return await _registry(request).apply_review_resolution(
        version_id, question_id
    )


@app.get("/versions/{version_id}/review-artifacts/{artifact_path:path}")
async def get_review_artifact(
    version_id: UUID, artifact_path: str, request: Request
) -> FileResponse:
    artifact = await _registry(request).review_artifact(
        version_id, artifact_path
    )
    return FileResponse(artifact, media_type="image/png")


@app.post(
    "/versions/{version_id}/approve",
    response_model=DocumentVersionResponse,
)
async def approve_version(
    version_id: UUID, request: Request
) -> DocumentVersionResponse:
    return await _registry(request).approve_version(version_id)


@app.delete(
    "/versions/{version_id}", response_model=DeleteDraftResponse
)
async def delete_draft(
    version_id: UUID, request: Request
) -> DeleteDraftResponse:
    await _registry(request).delete_draft(version_id)
    return DeleteDraftResponse(deleted_version_id=version_id)
