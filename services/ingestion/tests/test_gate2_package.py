from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.build_gate2_review_package import (
    _document_directory,
    _next_run_id,
    _review_markdown,
)


def test_gate2_document_directories_are_separated_by_type() -> None:
    assert _document_directory(
        Path("deck.pptx"), transcript_filename="transcript.pdf"
    ) == Path("pptx/deck")
    assert _document_directory(
        Path("transcript.pdf"), transcript_filename="transcript.pdf"
    ) == Path("transcript/transcript")
    assert _document_directory(
        Path("architecture.md"), transcript_filename="transcript.pdf"
    ) == Path("markdown-text/architecture")


def test_gate2_review_links_to_document_subfolder() -> None:
    markdown = _review_markdown(
        {
            "overall_status": "PASS",
            "documents": [
                {
                    "source_filename": "deck.pptx",
                    "source_path": "/samples/deck.pptx",
                    "document_directory": "pptx/deck",
                    "normalized_json": "pptx/deck/normalized.json",
                    "llm_report": "pptx/deck/PPTX-LLM-SLIDE-REPORT.md",
                    "pptx_review_report": "pptx/deck/PPTX-GATE-2-EXCEPTIONS.md",
                    "review_status": "NEEDS_HUMAN_REVIEW",
                    "review_severity": "HIGH",
                    "units_to_review": [14, 22],
                    "review_unit_label": "Slide",
                    "review_note": "Review representative slides.",
                }
            ],
        }
    )

    assert "**HUMAN REVIEW REQUIRED**" in markdown
    assert "**HIGH**" in markdown
    assert "**Slides 14, 22**" in markdown
    assert "[PPTX LLM slide report](<pptx/deck/PPTX-LLM-SLIDE-REPORT.md>)" in markdown
    assert "**PENDING**" in markdown


def test_gate2_run_id_is_human_readable_and_collision_safe(tmp_path: Path) -> None:
    timestamp = datetime(2026, 10, 14, 22, 55, tzinfo=ZoneInfo("Europe/Rome"))

    assert _next_run_id(tmp_path, timestamp) == "2026-10-14_22-55-00+0200"

    (tmp_path / "2026-10-14_22-55-00+0200").mkdir()
    assert _next_run_id(tmp_path, timestamp) == "2026-10-14_22-55-00+0200-2"
