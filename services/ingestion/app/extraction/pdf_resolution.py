from __future__ import annotations

import hashlib

from .models import NormalizedPage, SlideReviewDecisionFinding


def review_questions(page: NormalizedPage) -> list[dict]:
    if page.review is None or page.review.status != "REVIEW_REQUIRED":
        return []
    resolutions = {
        resolution.question_id: resolution
        for resolution in page.human_resolutions
    }
    questions = []
    for index, finding in enumerate(page.review.findings):
        if finding.severity in {"NONE", "LOW"}:
            continue
        question_id = _question_id(page.page_number, index, finding)
        questions.append(
            {
                "id": question_id,
                "finding_index": index,
                "page_number": page.page_number,
                "category": finding.category,
                "severity": finding.severity,
                "description": finding.description,
                "evidence": finding.evidence,
                "question": _question(finding),
                "resolution": (
                    resolutions[question_id].model_dump(mode="json")
                    if question_id in resolutions
                    else None
                ),
            }
        )
    return questions


def find_review_question(page: NormalizedPage, question_id: str) -> dict:
    for question in review_questions(page):
        if question["id"] == question_id:
            return question
    raise KeyError(question_id)


def _question_id(
    page_number: int,
    finding_index: int,
    finding: SlideReviewDecisionFinding,
) -> str:
    fingerprint = hashlib.sha256(
        f"{finding.category}\0{finding.description}\0{finding.evidence}".encode()
    ).hexdigest()[:12]
    return f"page-{page_number}-finding-{finding_index + 1}-{fingerprint}"


def _question(finding: SlideReviewDecisionFinding) -> str:
    detail = finding.description.rstrip(" .")
    if finding.category == "ATOMIC_FACT_CONFLICT":
        return (
            "Which exact fact should the normalized PDF page use for this "
            f"conflict: {detail}? Provide the correct wording, number, date, "
            "name, or label."
        )
    if finding.category == "INVENTED_RELATIONSHIP":
        return (
            f"What relationship, if any, should be recorded: {detail}? "
            "State the correct relationship or answer 'no relationship'."
        )
    if finding.category == "MISSING_KEY_ELEMENT":
        return (
            f"Should this element be included in the normalized page: {detail}? "
            "If yes, describe its exact meaning; otherwise answer 'exclude'."
        )
    return (
        f"Please clarify this uncertain PDF page content: {detail}. State what "
        "it means, or answer 'unknown/not relevant' if it should not be added."
    )
