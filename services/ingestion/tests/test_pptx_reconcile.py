from __future__ import annotations

from app.extraction.models import (
    SlideBoundingBox,
    SlideNativeElement,
    SlideVisionResult,
    SlideVisualElement,
)
from app.extraction.pptx_reconcile import reconcile_slide


def box(x: int, y: int, width: int = 300, height: int = 100) -> SlideBoundingBox:
    return SlideBoundingBox(x=x, y=y, width=width, height=height)


def vision(*elements: SlideVisualElement) -> SlideVisionResult:
    return SlideVisionResult(
        transcription_markdown="",
        visual_description="",
        elements=list(elements),
    )


def test_global_assignment_uses_geometry_for_repeated_text() -> None:
    native = [
        SlideNativeElement(
            element_id="native-top",
            kind="text",
            content="Status approved",
            bbox=box(50, 100),
        ),
        SlideNativeElement(
            element_id="native-bottom",
            kind="text",
            content="Status approved",
            bbox=box(50, 700),
        ),
    ]
    result = reconcile_slide(
        native_markdown="Status approved\n\nStatus approved",
        native_elements=native,
        speaker_notes=None,
        vision=vision(
            SlideVisualElement(
                kind="text", content="Status approved", bbox=box(50, 700)
            ),
            SlideVisualElement(
                kind="text", content="Status approved", bbox=box(50, 100)
            ),
        ),
    )

    assert result.version == "element-fusion-v3"
    assert result.alignments[0].native_element_ids == ["native-bottom"]
    assert result.alignments[1].native_element_ids == ["native-top"]
    assert all(item.match_method == "global" for item in result.alignments)
    assert all(item.relationship == "duplicate" for item in result.alignments)


def test_visual_diagram_is_retained_as_complement() -> None:
    result = reconcile_slide(
        native_markdown="# Architecture",
        native_elements=[
            SlideNativeElement(
                element_id="native-title",
                kind="title",
                content="# Architecture",
                bbox=box(50, 50, 800, 100),
            )
        ],
        speaker_notes=None,
        vision=vision(
            SlideVisualElement(
                kind="diagram",
                content="Requests flow from the UI to the supervisor and agents.",
                bbox=box(100, 250, 800, 500),
            )
        ),
    )

    alignment = result.alignments[0]
    assert alignment.relationship == "complement"
    assert alignment.match_method == "unmatched"
    assert result.included_visual_ids == ["visual-element-1"]
    assert "Requests flow from the UI" in result.retrieval_markdown


def test_numeric_mismatch_is_a_deterministic_conflict() -> None:
    result = reconcile_slide(
        native_markdown="Target completion: 2026",
        native_elements=[
            SlideNativeElement(
                element_id="native-target",
                kind="text",
                content="Target completion: 2026",
                bbox=box(100, 200, 600, 120),
            )
        ],
        speaker_notes=None,
        vision=vision(
            SlideVisualElement(
                kind="text",
                content="Target completion: 2028",
                bbox=box(100, 200, 600, 120),
            )
        ),
    )

    alignment = result.alignments[0]
    assert alignment.relationship == "conflict"
    assert alignment.match_method == "global"
    assert result.conflicts
    assert "2026" in result.conflicts[0]
    assert "2028" in result.conflicts[0]
    assert result.included_visual_ids == []
    assert result.quarantined_conflict_visual_ids == ["visual-element-1"]
    assert "2028" not in result.retrieval_markdown


def test_equivalent_thousands_format_is_not_a_conflict() -> None:
    result = reconcile_slide(
        native_markdown="Budget: $1,000",
        native_elements=[
            SlideNativeElement(
                element_id="native-budget",
                kind="text",
                content="Budget: $1,000",
                bbox=box(100, 200, 600, 120),
            )
        ],
        speaker_notes=None,
        vision=vision(
            SlideVisualElement(
                kind="text",
                content="Budget: $1000",
                bbox=box(100, 200, 600, 120),
            )
        ),
    )

    assert result.conflicts == []
    assert result.alignments[0].relationship == "duplicate"
