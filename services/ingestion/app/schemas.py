from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DocumentType(StrEnum):
    OFFICIAL = "official"
    INTERNAL = "internal"
    TRANSCRIPT = "transcript"
    ARTIFACT = "artifact"


class DocumentStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class DocumentDeletionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETING = "DELETING"
    DELETE_FAILED = "DELETE_FAILED"


class ProcessingStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


def _non_empty(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("value must not be empty")
    return cleaned


CONTENT_PROFILE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


def _content_profile(value: str) -> str:
    cleaned = value.strip().lower()
    if CONTENT_PROFILE.fullmatch(cleaned) is None:
        raise ValueError(
            "content_profile must use lower-case letters, numbers, and underscores"
        )
    return cleaned


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    _clean_name = field_validator("name")(_non_empty)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    document_type: DocumentType
    content_profile: str = "general_document"

    _clean_title = field_validator("title")(_non_empty)
    _clean_content_profile = field_validator("content_profile")(_content_profile)


class DocumentResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    document_type: DocumentType
    content_profile: str
    deletion_status: DocumentDeletionStatus
    deletion_error: str | None
    deletion_requested_at: datetime | None
    created_at: datetime


class DocumentDeletionResponse(BaseModel):
    document_id: UUID
    status: DocumentDeletionStatus
    version_count: int = Field(ge=0)


class DocumentVersionResponse(BaseModel):
    id: UUID
    document_id: UUID
    version_label: str
    status: DocumentStatus
    file_path: str
    content_hash: str
    source_filename: str
    created_at: datetime
    approved_at: datetime | None
    approved_by: str | None
    supersedes_version_id: UUID | None
    processing_status: ProcessingStatus
    content_profile: str
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    llm_metadata: dict[str, Any] = Field(default_factory=dict)


class DeleteDraftResponse(BaseModel):
    deleted_version_id: UUID


class ExtractionRunResponse(BaseModel):
    id: UUID
    document_version_id: UUID
    processing_status: ProcessingStatus
    extractor_version: str | None
    renderer_version: str | None
    vision_provider: str | None
    vision_model: str | None
    vision_prompt_version: str | None
    source_format: str | None
    normalized_path: str | None
    quality_report: dict[str, Any] | None
    error_code: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
