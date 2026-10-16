from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from app.storage import VersionStorage
from fastapi import UploadFile


@pytest.mark.asyncio
async def test_version_storage_uses_stable_ids_and_hashes_content(tmp_path) -> None:
    storage = VersionStorage(tmp_path)
    project_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    content = b"immutable document content"
    upload = UploadFile(file=BytesIO(content), filename="Client Plan.PDF")

    stored = await storage.save_upload(
        project_id, document_id, version_id, upload
    )

    assert stored.relative_path == (
        f"{project_id}/{document_id}/{version_id}/source.pdf"
    )
    assert stored.content_hash == hashlib.sha256(content).hexdigest()
    assert storage.content_hash(stored.relative_path) == stored.content_hash
    assert storage.exists(stored.relative_path)

    normalized_path = storage.write_normalized(
        stored.relative_path, {"schema_version": "1.0", "blocks": []}
    )
    assert storage.read_normalized(normalized_path)["schema_version"] == "1.0"

    report_path = storage.write_pptx_llm_report(
        stored.relative_path, "# PPTX LLM slide report\n"
    )
    assert report_path.endswith("/PPTX-LLM-SLIDE-REPORT.md")
    assert storage.absolute_path(report_path).read_text(encoding="utf-8") == (
        "# PPTX LLM slide report\n"
    )
    assert storage.read_pptx_llm_report(stored.relative_path) == (
        "# PPTX LLM slide report\n"
    )

    pdf_report_path = storage.write_pdf_llm_report(
        stored.relative_path, "# PDF LLM page report\n"
    )
    assert pdf_report_path.endswith("/PDF-LLM-PAGE-REPORT.md")
    assert storage.read_pdf_llm_report(stored.relative_path) == (
        "# PDF LLM page report\n"
    )
    docx_report_path = storage.write_docx_review_report(
        stored.relative_path, "# DOCX Word-page review report\n"
    )
    assert docx_report_path.endswith("/DOCX-WORD-PAGE-REVIEW-REPORT.md")
    assert storage.read_docx_review_report(stored.relative_path) == (
        "# DOCX Word-page review report\n"
    )
    xlsx_report_path = storage.write_xlsx_review_report(
        stored.relative_path, "# XLSX worksheet review report\n"
    )
    assert xlsx_report_path.endswith("/XLSX-WORKSHEET-REVIEW-REPORT.md")
    assert storage.read_xlsx_review_report(stored.relative_path) == (
        "# XLSX worksheet review report\n"
    )

    slide = storage.absolute_path(stored.relative_path).parent / "slides" / "slide-0001.png"
    slide.parent.mkdir()
    slide.write_bytes(b"png")
    assert storage.review_artifact(
        stored.relative_path, "slides/slide-0001.png"
    ) == slide
    page = storage.absolute_path(stored.relative_path).parent / "pages" / "page-0001.png"
    page.parent.mkdir()
    page.write_bytes(b"png")
    assert storage.review_artifact(
        stored.relative_path, "pages/page-0001.png"
    ) == page
    worksheet = (
        storage.absolute_path(stored.relative_path).parent
        / "worksheets"
        / "worksheet-0001.png"
    )
    worksheet.parent.mkdir(exist_ok=True)
    worksheet.write_bytes(b"png")
    assert storage.review_artifact(
        stored.relative_path, "worksheets/worksheet-0001.png"
    ) == worksheet
    with pytest.raises(ValueError, match="escapes"):
        storage.review_artifact(stored.relative_path, "../source.pdf")
    with pytest.raises(FileNotFoundError):
        storage.review_artifact(stored.relative_path, "source.pdf")

    await storage.delete_version(stored.relative_path)
    assert not storage.exists(stored.relative_path)
    assert not storage.absolute_path(normalized_path).exists()
    assert not storage.absolute_path(report_path).exists()


@pytest.mark.asyncio
async def test_document_storage_deletes_every_version(tmp_path) -> None:
    storage = VersionStorage(tmp_path)
    project_id, document_id = uuid4(), uuid4()
    first = await storage.save_upload(
        project_id,
        document_id,
        uuid4(),
        UploadFile(file=BytesIO(b"first"), filename="first.pdf"),
    )
    second = await storage.save_upload(
        project_id,
        document_id,
        uuid4(),
        UploadFile(file=BytesIO(b"second"), filename="second.pdf"),
    )

    await storage.delete_document(project_id, document_id)

    assert not storage.exists(first.relative_path)
    assert not storage.exists(second.relative_path)


@pytest.mark.asyncio
async def test_project_storage_deletes_every_document(tmp_path) -> None:
    storage = VersionStorage(tmp_path)
    project_id = uuid4()
    first = await storage.save_upload(
        project_id,
        uuid4(),
        uuid4(),
        UploadFile(file=BytesIO(b"first"), filename="first.pdf"),
    )
    second = await storage.save_upload(
        project_id,
        uuid4(),
        uuid4(),
        UploadFile(file=BytesIO(b"second"), filename="second.pdf"),
    )

    await storage.delete_project(project_id)

    assert not storage.exists(first.relative_path)
    assert not storage.exists(second.relative_path)


@pytest.mark.asyncio
async def test_version_storage_rejects_paths_outside_root(tmp_path) -> None:
    storage = VersionStorage(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        await storage.delete("../outside.txt")
