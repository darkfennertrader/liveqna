import pytest
from pydantic import ValidationError

from app.schemas import DocumentCreate, DocumentType, ProjectCreate


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
