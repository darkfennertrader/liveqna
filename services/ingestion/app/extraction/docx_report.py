from __future__ import annotations

from .docx_resolution import review_questions
from .models import NormalizedDocument, NormalizedPage

DOCX_REVIEW_REPORT_FILENAME = "DOCX-WORD-PAGE-REVIEW-REPORT.md"


def build_docx_review_report(document: NormalizedDocument) -> str:
    if document.source_format != "docx" or not document.pages:
        raise ValueError("DOCX review report requires normalized Word pages")
    metadata = document.docx_metadata
    lines = [
        "# DOCX Word-page review report",
        "",
        f"Source: `{document.source_filename}`",
        f"Content hash: `{document.content_hash}`",
        "",
        (
            "Every rendered page was compared with authoritative OOXML extraction, "
            "deterministically reconciled, and checked by an independent multimodal "
            "judge. Native Word text and tables retain precedence."
        ),
        "",
        "## Native DOCX inventory",
        "",
        f"- Revision policy: `{metadata.revision_policy if metadata else 'unknown'}`",
        f"- Sections: {metadata.section_count if metadata else 0}",
        f"- Paragraphs: {metadata.paragraph_count if metadata else 0}",
        f"- Tables: {metadata.table_count if metadata else 0}",
        f"- Images: {metadata.image_count if metadata else 0}",
        f"- Content controls: {metadata.content_control_count if metadata else 0}",
        f"- Comments: {metadata.comment_count if metadata else 0}",
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
        f"## Word page {page.page_number}",
        "",
        f"![Rendered Word page {page.page_number}]({page.image_path})",
        "",
        "### Authoritative OOXML extraction",
        "",
        page.native_markdown or "_(No page-mapped native text.)_",
        "",
        "### Independent visual extraction",
        "",
        page.vision.transcription_markdown or "_(No visual transcription.)_",
        "",
        page.vision.visual_description or "_(No visual description.)_",
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
                    "  - Status: "
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
            (
                f"- Visual provider/model: `{page.vision_provenance.provider}` / "
                f"`{page.vision_provenance.model}`"
            ),
            f"- Visual prompt: `{page.vision_provenance.prompt_version}`",
            f"- Render version: `{page.render_version}`",
        ]
    )
    if page.review and page.review.judge_provenance:
        lines.extend(
            [
                (
                    "- Judge provider/model: "
                    f"`{page.review.judge_provenance.provider}` / "
                    f"`{page.review.judge_provenance.model}`"
                ),
                f"- Judge prompt: `{page.review.judge_provenance.prompt_version}`",
            ]
        )
    return lines
