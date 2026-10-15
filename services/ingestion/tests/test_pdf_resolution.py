from app.extraction.models import (
    NormalizedPage,
    SlideReviewDecision,
    SlideReviewDecisionFinding,
    SlideVisionProvenance,
    SlideVisionResult,
)
from app.extraction.pdf_resolution import review_questions


def test_pdf_review_question_is_page_specific() -> None:
    page = NormalizedPage(
        page_number=4,
        native_markdown="Native value 42",
        native_text_character_count=15,
        max_image_pixels=0,
        vector_operation_count=0,
        image_path="pages/page-0004.png",
        image_hash="hash",
        render_version="pymupdf-png-v1",
        vision=SlideVisionResult(
            transcription_markdown="Native value 24",
            visual_description="",
        ),
        vision_provenance=SlideVisionProvenance(
            provider="fake",
            model="fake",
            prompt_version="pdf-page-v2",
            response_hash="hash",
            duration_ms=1,
        ),
        review=SlideReviewDecision(
            version="pdf-page-review-v1",
            status="REVIEW_REQUIRED",
            severity="CRITICAL",
            findings=[
                SlideReviewDecisionFinding(
                    category="ATOMIC_FACT_CONFLICT",
                    severity="CRITICAL",
                    source="judge",
                    description="Native value 42 conflicts with visual value 24.",
                    evidence="The two evidence streams contain different values.",
                )
            ],
        ),
        combined_markdown="Native value 42",
    )

    questions = review_questions(page)

    assert len(questions) == 1
    assert questions[0]["id"].startswith("page-4-finding-1-")
    assert questions[0]["page_number"] == 4
    assert "Which exact fact" in questions[0]["question"]
