from __future__ import annotations

from typing import Literal

from .models import (
    SlideReconciliation,
    SlideReviewDecision,
    SlideReviewDecisionFinding,
    SlideReviewFinding,
    SlideReviewProvenance,
    SlideReviewResult,
    SlideReviewSeverity,
    SlideVisionResult,
)

REVIEW_VERSION = "slide-review-v1"
SEVERITY_BY_CATEGORY: dict[str, SlideReviewSeverity] = {
    "ATOMIC_FACT_CONFLICT": "CRITICAL",
    "INVENTED_RELATIONSHIP": "CRITICAL",
    "MISSING_KEY_ELEMENT": "HIGH",
    "PARTIAL_OR_UNCERTAIN": "MEDIUM",
    "FORMAT_ONLY": "LOW",
    "NO_DISCREPANCY": "NONE",
}
_SEVERITY_RANK = {name: index for index, name in enumerate(
    ("NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL")
)}


def decide_slide_review(
    *,
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
    judge_result: SlideReviewResult,
    judge_provenance: SlideReviewProvenance,
) -> SlideReviewDecision:
    return _decide_review(
        vision=vision,
        reconciliation=reconciliation,
        judge_result=judge_result,
        judge_provenance=judge_provenance,
        version=REVIEW_VERSION,
    )


def decide_page_review(
    *,
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
    judge_result: SlideReviewResult,
    judge_provenance: SlideReviewProvenance,
) -> SlideReviewDecision:
    return _decide_review(
        vision=vision,
        reconciliation=reconciliation,
        judge_result=judge_result,
        judge_provenance=judge_provenance,
        version="pdf-page-review-v1",
    )


def decide_worksheet_review(
    *,
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
    judge_result: SlideReviewResult,
    judge_provenance: SlideReviewProvenance,
    deterministic_findings: list[SlideReviewDecisionFinding] | None = None,
) -> SlideReviewDecision:
    decision = _decide_review(
        vision=vision,
        reconciliation=reconciliation,
        judge_result=judge_result,
        judge_provenance=judge_provenance,
        version="xlsx-worksheet-review-v1",
    )
    findings = _deduplicate(
        [*(deterministic_findings or []), *decision.findings]
    )
    severity = max(
        (finding.severity for finding in findings),
        key=lambda value: _SEVERITY_RANK[value],
        default="NONE",
    )
    return decision.model_copy(
        update={
            "findings": findings,
            "severity": severity,
            "status": (
                "REVIEW_REQUIRED"
                if _SEVERITY_RANK[severity] >= _SEVERITY_RANK["MEDIUM"]
                else "AUTO_PASS"
            ),
        }
    )


def decide_word_page_review(
    *,
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
    judge_result: SlideReviewResult,
    judge_provenance: SlideReviewProvenance,
    deterministic_findings: list[SlideReviewDecisionFinding] | None = None,
) -> SlideReviewDecision:
    decision = _decide_review(
        vision=vision,
        reconciliation=reconciliation,
        judge_result=judge_result,
        judge_provenance=judge_provenance,
        version="docx-page-review-v1",
    )
    findings = _deduplicate(
        [*(deterministic_findings or []), *decision.findings]
    )
    severity = max(
        (finding.severity for finding in findings),
        key=lambda value: _SEVERITY_RANK[value],
        default="NONE",
    )
    return decision.model_copy(
        update={
            "findings": findings,
            "severity": severity,
            "status": (
                "REVIEW_REQUIRED"
                if _SEVERITY_RANK[severity] >= _SEVERITY_RANK["MEDIUM"]
                else "AUTO_PASS"
            ),
        }
    )


def _decide_review(
    *,
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
    judge_result: SlideReviewResult,
    judge_provenance: SlideReviewProvenance,
    version: str,
) -> SlideReviewDecision:
    findings: list[SlideReviewDecisionFinding] = []

    for conflict in reconciliation.conflicts:
        findings.append(
            _decision_finding(
                SlideReviewFinding(
                    category="ATOMIC_FACT_CONFLICT",
                    description=conflict,
                    evidence="Deterministic native/visual reconciliation conflict.",
                ),
                source="deterministic",
            )
        )

    for uncertainty in vision.uncertainties:
        findings.append(
            _decision_finding(
                SlideReviewFinding(
                    category="PARTIAL_OR_UNCERTAIN",
                    description=uncertainty,
                    evidence="First-pass visual extraction reported uncertainty.",
                ),
                source="deterministic",
            )
        )

    judge_findings = judge_result.findings
    has_no_discrepancy = any(
        finding.category == "NO_DISCREPANCY" for finding in judge_findings
    )
    has_discrepancy = any(
        finding.category != "NO_DISCREPANCY" for finding in judge_findings
    )
    if has_no_discrepancy and has_discrepancy:
        findings.append(
            _system_finding(
                "Judge returned NO_DISCREPANCY together with discrepancy findings."
            )
        )
    else:
        findings.extend(
            _decision_finding(finding, source="judge")
            for finding in judge_findings
            if finding.category != "NO_DISCREPANCY"
        )

    findings = _deduplicate(findings)
    severity = max(
        (finding.severity for finding in findings),
        key=lambda value: _SEVERITY_RANK[value],
        default="NONE",
    )
    status = (
        "REVIEW_REQUIRED"
        if _SEVERITY_RANK[severity] >= _SEVERITY_RANK["MEDIUM"]
        else "AUTO_PASS"
    )
    return SlideReviewDecision(
        version=version,
        status=status,
        severity=severity,
        findings=findings,
        judge_result=judge_result,
        judge_provenance=judge_provenance,
    )


def failed_slide_review(message: str) -> SlideReviewDecision:
    finding = _system_finding(message)
    return SlideReviewDecision(
        version=REVIEW_VERSION,
        status="REVIEW_REQUIRED",
        severity=finding.severity,
        findings=[finding],
    )


def failed_page_review(message: str) -> SlideReviewDecision:
    finding = _system_finding(message)
    return SlideReviewDecision(
        version="pdf-page-review-v1",
        status="REVIEW_REQUIRED",
        severity=finding.severity,
        findings=[finding],
    )


def failed_worksheet_review(message: str) -> SlideReviewDecision:
    finding = _system_finding(message)
    return SlideReviewDecision(
        version="xlsx-worksheet-review-v1",
        status="REVIEW_REQUIRED",
        severity=finding.severity,
        findings=[finding],
    )


def failed_word_page_review(message: str) -> SlideReviewDecision:
    finding = _system_finding(message)
    return SlideReviewDecision(
        version="docx-page-review-v1",
        status="REVIEW_REQUIRED",
        severity=finding.severity,
        findings=[finding],
    )


def _decision_finding(
    finding: SlideReviewFinding,
    *,
    source: Literal["deterministic", "judge", "system"],
) -> SlideReviewDecisionFinding:
    return SlideReviewDecisionFinding(
        category=finding.category,
        severity=SEVERITY_BY_CATEGORY[finding.category],
        source=source,
        description=finding.description,
        evidence=finding.evidence,
        native_element_ids=finding.native_element_ids,
        visual_element_ids=finding.visual_element_ids,
    )


def _system_finding(message: str) -> SlideReviewDecisionFinding:
    return SlideReviewDecisionFinding(
        category="PARTIAL_OR_UNCERTAIN",
        severity="MEDIUM",
        source="system",
        description=message,
        evidence="The independent review result could not be safely accepted.",
    )


def _deduplicate(
    findings: list[SlideReviewDecisionFinding],
) -> list[SlideReviewDecisionFinding]:
    unique: dict[tuple[str, str], SlideReviewDecisionFinding] = {}
    for finding in findings:
        key = (finding.category, finding.description.casefold().strip())
        unique.setdefault(key, finding)
    return list(unique.values())
