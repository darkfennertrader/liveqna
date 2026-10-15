import pytest
from pydantic import ValidationError

from app.schemas import (
    DocumentCreate,
    DocumentType,
    ProjectCreate,
    ProjectDeletionResponse,
)


def test_registry_names_are_trimmed() -> None:
    assert ProjectCreate(name="  Client AI  ").name == "Client AI"
    document = DocumentCreate(
        title="  Target architecture  ", document_type="official"
    )
    assert document.title == "Target architecture"
    assert document.document_type is DocumentType.OFFICIAL


def test_registry_names_reject_whitespace_only() -> None:
    with pytest.raises(ValidationError):
        ProjectCreate(name="   ")


def test_project_deletion_counts_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        ProjectDeletionResponse(
            project_id="90285027-e928-4c5b-ada0-4b38ae2e89d4",
            status="DELETING",
            document_count=-1,
            version_count=0,
        )
