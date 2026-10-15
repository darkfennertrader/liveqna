from __future__ import annotations

from app.extraction.models import (
    NormalizedSlide,
    SlideVisionProvenance,
    SlideVisionResult,
)
from app.extraction.pptx_link import link_slides


def slide(
    number: int,
    *,
    title: str,
    content: str,
    labels: list[str],
) -> NormalizedSlide:
    vision = SlideVisionResult(
        transcription_markdown=content,
        visual_description="",
        labels=labels,
    )
    return NormalizedSlide(
        slide_number=number,
        title=title,
        native_markdown=content,
        image_path=f"slides/slide-{number:04d}.png",
        image_hash=f"hash-{number}",
        render_version="test-renderer",
        vision=vision,
        vision_provenance=SlideVisionProvenance(
            provider="test",
            model="test-model",
            prompt_version="test-prompt",
            response_hash=f"response-{number}",
            duration_ms=1,
        ),
        combined_markdown=content + "\n\n" + ", ".join(labels),
    )


def relation(slide_record: NormalizedSlide, relationship_type: str, target: int):
    return next(
        item
        for item in slide_record.relationships
        if item.type == relationship_type and item.target_slide == target
    )


def test_linker_adds_order_and_explicit_reference_links() -> None:
    linked = link_slides(
        [
            slide(
                1,
                title="Architecture overview",
                content=(
                    "Supervisor routes approved questions to retrieval. "
                    "See slide 3 for financial details."
                ),
                labels=["architecture", "supervisor"],
            ),
            slide(
                2,
                title="Architecture overview continued",
                content="Supervisor routes validated questions to retrieval.",
                labels=["architecture", "supervisor"],
            ),
            slide(
                3,
                title="Financial details",
                content="Quarterly revenue and cost figures.",
                labels=["finance"],
            ),
        ]
    )

    assert relation(linked[0], "next", 2).confidence == 1.0
    assert relation(linked[1], "previous", 1).confidence == 1.0
    assert relation(linked[1], "next", 3).confidence == 1.0
    assert relation(linked[0], "references", 3).method == "deterministic"
    assert relation(linked[0], "references", 3).linker_version == "slide-linker-v1"
    assert relation(linked[2], "referenced_by", 1).confidence == 1.0


def test_weighted_relationship_confidence_equals_signal_contributions() -> None:
    linked = link_slides(
        [
            slide(
                1,
                title="Architecture overview",
                content="Supervisor routes approved questions to retrieval.",
                labels=["architecture", "supervisor"],
            ),
            slide(
                2,
                title="Architecture overview continued",
                content="Supervisor routes validated questions to retrieval.",
                labels=["architecture", "supervisor"],
            ),
        ]
    )

    same_section = relation(linked[0], "same_section", 2)
    continues = relation(linked[0], "continues", 2)
    continued_from = relation(linked[1], "continued_from", 1)

    assert same_section.method == "weighted_signals"
    assert same_section.confidence == round(
        sum(item.contribution for item in same_section.signals), 4
    )
    assert continues.confidence == round(
        sum(item.contribution for item in continues.signals), 4
    )
    assert continued_from.confidence == continues.confidence
    assert {item.name for item in continues.signals} == {
        "adjacency",
        "title_similarity",
        "shared_entities",
        "content_similarity",
    }


def test_order_links_require_consecutive_source_slide_numbers() -> None:
    linked = link_slides(
        [
            slide(5, title="Overview", content="First sample.", labels=[]),
            slide(14, title="Diagram", content="Second sample.", labels=[]),
        ]
    )

    assert all(item.type not in {"previous", "next"} for item in linked[0].relationships)
    assert all(item.type not in {"previous", "next"} for item in linked[1].relationships)
