from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .database import Database
from .errors import RegistryConflictError, RegistryNotFoundError
from .extraction import ExtractionPipeline, NormalizedDocument
from .extraction.pdf_renderer import PdftoppmPdfRenderer
from .extraction.pptx_renderer import LibreOfficePptxRenderer
from .qdrant import QdrantProbe
from .repository import RegistryRepository
from .schemas import (
    DeleteDraftResponse,
    DocumentCreate,
    DocumentDeletionResponse,
    DocumentResponse,
    DocumentVersionResponse,
    ExtractionRunResponse,
    ProjectCreate,
    ProjectResponse,
)
from .service import RegistryService
from .settings import Settings
from .storage import VersionStorage
from .vision import create_review_provider, create_vision_provider


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
            pdf_renderer=PdftoppmPdfRenderer(
                pdftoppm_binary=settings.pdf_pdftoppm_binary,
                dpi=settings.pdf_render_dpi,
                timeout_seconds=settings.pdf_render_timeout_seconds,
            ),
            pptx_renderer=LibreOfficePptxRenderer(
                soffice_binary=settings.pptx_soffice_binary,
                pdftoppm_binary=settings.pptx_pdftoppm_binary,
                dpi=settings.pptx_render_dpi,
                timeout_seconds=settings.pptx_render_timeout_seconds,
            ),
            vision_provider=create_vision_provider(settings),
            review_provider=create_review_provider(settings),
            pdf_vision_prompt_version=settings.pdf_vision_prompt_version,
            vision_prompt_version=settings.vision_prompt_version,
            review_prompt_version=settings.review_prompt_version,
            vision_max_concurrency=settings.vision_max_concurrency,
        ),
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
