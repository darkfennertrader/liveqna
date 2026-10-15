from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.storage import VersionStorage


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
async def test_version_storage_rejects_paths_outside_root(tmp_path) -> None:
    storage = VersionStorage(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        await storage.delete("../outside.txt")
