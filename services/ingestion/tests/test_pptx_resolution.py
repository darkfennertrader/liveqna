from app.extraction.models import (
    NormalizedSlide,
    SlideReviewDecision,
    SlideReviewDecisionFinding,
    SlideVisionProvenance,
    SlideVisionResult,
)
from app.extraction.pptx_resolution import review_questions


def _slide() -> NormalizedSlide:
    return NormalizedSlide(
        slide_number=6,
        title="Security boundaries",
        native_markdown="# Security boundaries",
        image_path="slides/slide-0006.png",
        image_hash="abc",
        render_version="test",
        vision=SlideVisionResult(
            transcription_markdown="# Security boundaries",
            visual_description="A diagram with a red numbered annotation.",
        ),
        vision_provenance=SlideVisionProvenance(
            provider="fake",
            model="vision",
            prompt_version="v1",
            response_hash="abc",
            duration_ms=1,
        ),
        review=SlideReviewDecision(
            status="REVIEW_REQUIRED",
            severity="MEDIUM",
            findings=[
                SlideReviewDecisionFinding(
                    category="PARTIAL_OR_UNCERTAIN",
                    severity="MEDIUM",
                    source="judge",
                    description="The red annotation meaning is unclear.",
                    evidence="The rendered slide contains a red circle.",
                )
            ],
        ),
        combined_markdown="# Security boundaries",
    )


def test_review_question_is_stable_and_actionable() -> None:
    first = review_questions(_slide())
    second = review_questions(_slide())

    assert first[0]["id"] == second[0]["id"]
    assert first[0]["severity"] == "MEDIUM"
    assert "Please clarify" in first[0]["question"]
    assert first[0]["resolution"] is None
