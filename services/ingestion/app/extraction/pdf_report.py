from __future__ import annotations

from .models import NormalizedDocument, NormalizedPage
from .pdf_resolution import review_questions


PDF_LLM_REPORT_FILENAME = "PDF-LLM-PAGE-REPORT.md"


def build_pdf_llm_report(document: NormalizedDocument) -> str:
    if document.source_format != "pdf" or not document.pages:
        raise ValueError("PDF LLM report requires normalized PDF pages")
    lines = [
        "# PDF LLM page report",
        "",
        f"Source: `{document.source_filename}`",
        f"Content hash: `{document.content_hash}`",
        "",
        "Every page was independently extracted through the native PDF layout "
        "and rendered-image paths, deterministically reconciled, and checked by "
        "an independent multimodal judge.",
        "",
        "## Automated review summary",
        "",
        "| Page | Status | Severity | Reason |",
        "| ---: | --- | --- | --- |",
    ]
    for page in document.pages:
        status = page.review.status if page.review else "REVIEW_REQUIRED"
        severity = page.review.severity if page.review else "MEDIUM"
        reasons = (
            "; ".join(finding.description for finding in page.review.findings)
            if page.review and page.review.findings
            else "No discrepancy found."
        )
        lines.append(
            f"| {page.page_number} | **{status}** | **{severity}** | "
            f"{reasons.replace('|', '\\|').replace(chr(10), ' ')} |"
        )
    for page in document.pages:
        lines.extend(_page_section(page))
    return "\n".join(lines).rstrip() + "\n"


def _page_section(page: NormalizedPage) -> list[str]:
    lines = [
        "",
        "---",
        "",
        f"## Page {page.page_number}",
        "",
        f"![Rendered page {page.page_number}]({page.image_path})",
        "",
        "### Native layout extraction",
        "",
        page.native_markdown or "_(No native text.)_",
        "",
        "### Independent visual extraction",
        "",
        page.vision.transcription_markdown or "_(No visual transcription.)_",
        "",
        page.vision.visual_description or "_(No visual description.)_",
        "",
        "### Reconciliation",
        "",
        f"- Strategy: `{page.reconciliation.version if page.reconciliation else 'missing'}`",
        "- Native elements: " + str(len(page.native_elements)),
        "- Visual elements: " + str(len(page.vision.elements)),
        "- Quarantined conflicts: "
        + (
            ", ".join(page.reconciliation.quarantined_conflict_visual_ids)
            if page.reconciliation
            and page.reconciliation.quarantined_conflict_visual_ids
            else "none"
        ),
        "",
        "### Independent judge",
        "",
    ]
    if page.review is None:
        lines.append("- REVIEW_REQUIRED: the independent judge did not complete.")
    else:
        lines.extend(
            [
                f"- Status: **{page.review.status}**",
                f"- Severity: **{page.review.severity}**",
            ]
        )
        for finding in page.review.findings:
            lines.append(
                f"- `{finding.category}` ({finding.source}): {finding.description}"
            )
    questions = review_questions(page)
    lines.extend(["", "### Human resolution questions", ""])
    if not questions:
        lines.append("- None. This page passed automatically.")
    else:
        for question in questions:
            lines.extend(
                [
                    f"- **{question['severity']}**: {question['question']}",
                    f"  - Evidence: {question['evidence']}",
                    f"  - Status: "
                    + (
                        question["resolution"]["status"]
                        if question["resolution"]
                        else "UNANSWERED"
                    ),
                ]
            )
    lines.extend(
        [
            "",
            "### Final reconciled output",
            "",
            page.combined_markdown or "_(Empty.)_",
            "",
            "### Provenance",
            "",
            f"- Visual provider/model: `{page.vision_provenance.provider}` / "
            f"`{page.vision_provenance.model}`",
            f"- Visual prompt: `{page.vision_provenance.prompt_version}`",
            f"- Render version: `{page.render_version}`",
        ]
    )
    if page.review and page.review.judge_provenance:
        lines.extend(
            [
                f"- Judge provider/model: `{page.review.judge_provenance.provider}` / "
                f"`{page.review.judge_provenance.model}`",
                f"- Judge prompt: `{page.review.judge_provenance.prompt_version}`",
            ]
        )
    return lines
