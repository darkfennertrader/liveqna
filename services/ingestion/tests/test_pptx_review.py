from app.extraction.models import (
    SlideReconciliation,
    SlideReviewFinding,
    SlideReviewProvenance,
    SlideReviewResult,
    SlideVisionResult,
)
from app.extraction.pptx_review import decide_slide_review


def provenance() -> SlideReviewProvenance:
    return SlideReviewProvenance(
        provider="fake",
        model="review-v1",
        prompt_version="pptx-slide-review-v1",
        response_hash="abc",
        duration_ms=10,
    )


def test_no_discrepancy_auto_passes() -> None:
    decision = decide_slide_review(
        vision=SlideVisionResult(
            transcription_markdown="Text", visual_description=""
        ),
        reconciliation=SlideReconciliation(retrieval_markdown="Text"),
        judge_result=SlideReviewResult(
            summary="Evidence agrees.",
            findings=[
                SlideReviewFinding(
                    category="NO_DISCREPANCY",
                    description="No discrepancy found.",
                    evidence="The evidence agrees.",
                )
            ],
        ),
        judge_provenance=provenance(),
    )

    assert decision.status == "AUTO_PASS"
    assert decision.severity == "NONE"
    assert decision.findings == []


def test_atomic_fact_conflict_is_critical_and_requires_review() -> None:
    decision = decide_slide_review(
        vision=SlideVisionResult(
            transcription_markdown="Target 2028", visual_description=""
        ),
        reconciliation=SlideReconciliation(
            conflicts=["Native 2026 versus visual 2028"],
            retrieval_markdown="Target 2026",
        ),
        judge_result=SlideReviewResult(
            summary="A number conflicts.",
            findings=[
                SlideReviewFinding(
                    category="ATOMIC_FACT_CONFLICT",
                    description="The target year conflicts.",
                    evidence="Native is 2026; rendered claim is 2028.",
                )
            ],
        ),
        judge_provenance=provenance(),
    )

    assert decision.status == "REVIEW_REQUIRED"
    assert decision.severity == "CRITICAL"
    assert {finding.source for finding in decision.findings} == {
        "deterministic",
        "judge",
    }


def test_format_only_auto_passes_with_low_severity() -> None:
    decision = decide_slide_review(
        vision=SlideVisionResult(
            transcription_markdown="Text", visual_description=""
        ),
        reconciliation=SlideReconciliation(retrieval_markdown="Text"),
        judge_result=SlideReviewResult(
            summary="Only formatting differs.",
            findings=[
                SlideReviewFinding(
                    category="FORMAT_ONLY",
                    description="Line wrapping differs.",
                    evidence="The wording is identical.",
                )
            ],
        ),
        judge_provenance=provenance(),
    )

    assert decision.status == "AUTO_PASS"
    assert decision.severity == "LOW"
