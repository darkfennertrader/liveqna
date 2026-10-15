from __future__ import annotations

import hashlib

from .models import NormalizedWorksheet, SlideReviewDecisionFinding


def review_questions(worksheet: NormalizedWorksheet) -> list[dict]:
    if worksheet.review is None or worksheet.review.status != "REVIEW_REQUIRED":
        return []
    resolutions = {
        resolution.question_id: resolution
        for resolution in worksheet.human_resolutions
    }
    questions = []
    for index, finding in enumerate(worksheet.review.findings):
        if finding.severity in {"NONE", "LOW"}:
            continue
        question_id = _question_id(worksheet.worksheet_index, index, finding)
        questions.append(
            {
                "id": question_id,
                "finding_index": index,
                "worksheet_index": worksheet.worksheet_index,
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


def find_review_question(
    worksheet: NormalizedWorksheet, question_id: str
) -> dict:
    for question in review_questions(worksheet):
        if question["id"] == question_id:
            return question
    raise KeyError(question_id)


def _question_id(
    worksheet_index: int,
    finding_index: int,
    finding: SlideReviewDecisionFinding,
) -> str:
    fingerprint = hashlib.sha256(
        f"{finding.category}\0{finding.description}\0{finding.evidence}".encode()
    ).hexdigest()[:12]
    return (
        f"worksheet-{worksheet_index}-finding-{finding_index + 1}-{fingerprint}"
    )


def _question(finding: SlideReviewDecisionFinding) -> str:
    detail = finding.description.rstrip(" .")
    if finding.category == "ATOMIC_FACT_CONFLICT":
        return (
            "Which exact worksheet value should be authoritative for this "
            f"conflict: {detail}? Provide the correct value and cell/range."
        )
    if finding.category == "INVENTED_RELATIONSHIP":
        return (
            f"What relationship, if any, should be recorded: {detail}? State "
            "the correct relationship or answer 'no relationship'."
        )
    if finding.category == "MISSING_KEY_ELEMENT":
        return (
            f"How should this missing worksheet dependency be handled: {detail}? "
            "Provide the authoritative meaning/value or answer 'exclude'."
        )
    return (
        f"Please clarify this uncertain worksheet evidence: {detail}. State the "
        "correct table/header/value meaning or answer 'unknown/not relevant'."
    )
