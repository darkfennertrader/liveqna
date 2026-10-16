from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from shutil import rmtree
from typing import BinaryIO
from uuid import UUID

from fastapi import UploadFile

from .extraction.docx_report import DOCX_REVIEW_REPORT_FILENAME
from .extraction.pdf_report import PDF_LLM_REPORT_FILENAME
from .extraction.pptx_report import PPTX_LLM_REPORT_FILENAME
from .extraction.xlsx_report import XLSX_LLM_REPORT_FILENAME

SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    content_hash: str


def _write_and_hash(source: BinaryIO, destination: Path) -> str:
    digest = hashlib.sha256()
    with destination.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    return digest.hexdigest()


class VersionStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def save_upload(
        self,
        project_id: UUID,
        document_id: UUID,
        version_id: UUID,
        upload: UploadFile,
    ) -> StoredFile:
        source_filename = upload.filename or ""
        suffix = Path(source_filename).suffix.lower()
        if SAFE_SUFFIX.fullmatch(suffix) is None:
            suffix = ""

        version_directory = (
            self._root / str(project_id) / str(document_id) / str(version_id)
        )
        version_directory.mkdir(parents=True, exist_ok=False)
        temporary_path = version_directory / ".uploading"
        final_path = version_directory / f"source{suffix}"

        try:
            upload.file.seek(0)
            content_hash = _write_and_hash(upload.file, temporary_path)
            os.replace(temporary_path, final_path)
        except Exception:
            rmtree(version_directory, ignore_errors=True)
            raise

        return StoredFile(
            relative_path=final_path.relative_to(self._root).as_posix(),
            content_hash=content_hash,
        )

    async def delete(self, relative_path: str) -> None:
        target = self._safe_path(relative_path)
        target.unlink(missing_ok=True)
        self._remove_empty_parents(target.parent)

    async def delete_version(self, relative_path: str) -> None:
        target = self._safe_path(relative_path)
        version_directory = target.parent
        if version_directory == self._root:
            raise ValueError("refusing to delete the storage root")
        rmtree(version_directory, ignore_errors=True)
        self._remove_empty_parents(version_directory.parent)

    async def delete_document(
        self, project_id: UUID, document_id: UUID
    ) -> None:
        document_directory = self._safe_path(
            f"{project_id}/{document_id}"
        )
        if document_directory == self._root:
            raise ValueError("refusing to delete the storage root")
        rmtree(document_directory, ignore_errors=True)
        self._remove_empty_parents(document_directory.parent)

    async def delete_project(self, project_id: UUID) -> None:
        project_directory = self._safe_path(str(project_id))
        if project_directory == self._root:
            raise ValueError("refusing to delete the storage root")
        rmtree(project_directory, ignore_errors=True)

    def absolute_path(self, relative_path: str) -> Path:
        return self._safe_path(relative_path)

    def write_normalized(self, source_path: str, payload: dict) -> str:
        source = self._safe_path(source_path)
        normalized = source.parent / "normalized.json"
        temporary = source.parent / ".normalized.json.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, normalized)
        return normalized.relative_to(self._root).as_posix()

    def write_pptx_llm_report(self, source_path: str, report: str) -> str:
        source = self._safe_path(source_path)
        destination = source.parent / PPTX_LLM_REPORT_FILENAME
        temporary = source.parent / f".{PPTX_LLM_REPORT_FILENAME}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination.relative_to(self._root).as_posix()

    def read_pptx_llm_report(self, source_path: str) -> str:
        source = self._safe_path(source_path)
        report = source.parent / PPTX_LLM_REPORT_FILENAME
        return report.read_text(encoding="utf-8")

    def write_pdf_llm_report(self, source_path: str, report: str) -> str:
        source = self._safe_path(source_path)
        destination = source.parent / PDF_LLM_REPORT_FILENAME
        temporary = source.parent / f".{PDF_LLM_REPORT_FILENAME}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination.relative_to(self._root).as_posix()

    def read_pdf_llm_report(self, source_path: str) -> str:
        source = self._safe_path(source_path)
        report = source.parent / PDF_LLM_REPORT_FILENAME
        return report.read_text(encoding="utf-8")

    def write_xlsx_review_report(self, source_path: str, report: str) -> str:
        source = self._safe_path(source_path)
        destination = source.parent / XLSX_LLM_REPORT_FILENAME
        temporary = source.parent / f".{XLSX_LLM_REPORT_FILENAME}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination.relative_to(self._root).as_posix()

    def read_xlsx_review_report(self, source_path: str) -> str:
        source = self._safe_path(source_path)
        report = source.parent / XLSX_LLM_REPORT_FILENAME
        return report.read_text(encoding="utf-8")

    def write_docx_review_report(self, source_path: str, report: str) -> str:
        source = self._safe_path(source_path)
        destination = source.parent / DOCX_REVIEW_REPORT_FILENAME
        temporary = source.parent / f".{DOCX_REVIEW_REPORT_FILENAME}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(report)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination.relative_to(self._root).as_posix()

    def read_docx_review_report(self, source_path: str) -> str:
        source = self._safe_path(source_path)
        report = source.parent / DOCX_REVIEW_REPORT_FILENAME
        return report.read_text(encoding="utf-8")

    def review_artifact(self, source_path: str, artifact_path: str) -> Path:
        source = self._safe_path(source_path)
        version_directory = source.parent.resolve()
        supplied = Path(artifact_path)
        if supplied.is_absolute():
            raise ValueError("review artifact path must be relative")
        artifact = (version_directory / supplied).resolve()
        if version_directory not in artifact.parents:
            raise ValueError("review artifact path escapes the document version")
        if artifact.suffix.lower() != ".png" or not artifact.is_file():
            raise FileNotFoundError(artifact_path)
        return artifact

    def read_normalized(self, relative_path: str) -> dict:
        normalized = self._safe_path(relative_path)
        with normalized.open(encoding="utf-8") as handle:
            return json.load(handle)

    def exists(self, relative_path: str) -> bool:
        return self._safe_path(relative_path).is_file()

    def content_hash(self, relative_path: str) -> str:
        source = self._safe_path(relative_path)
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _safe_path(self, relative_path: str) -> Path:
        supplied = Path(relative_path)
        if supplied.is_absolute():
            raise ValueError("stored file path must be relative")
        target = (self._root / supplied).resolve()
        if self._root not in target.parents:
            raise ValueError("stored file path escapes the storage root")
        return target

    def _remove_empty_parents(self, directory: Path) -> None:
        current = directory
        while current != self._root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
