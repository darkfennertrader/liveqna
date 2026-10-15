from __future__ import annotations

from .models import NormalizedDocument, NormalizedWorksheet
from .xlsx_resolution import review_questions


XLSX_LLM_REPORT_FILENAME = "XLSX-WORKSHEET-REVIEW-REPORT.md"


def build_xlsx_review_report(document: NormalizedDocument) -> str:
    if document.source_format != "xlsx" or not document.worksheets:
        raise ValueError("XLSX report requires normalized worksheets")
    lines = [
        "# XLSX worksheet review report",
        "",
        f"Source: `{document.source_filename}`",
        f"Content hash: `{document.content_hash}`",
        "",
        "Native cells, formulas, tables, names, and workbook structure remain "
        "authoritative. LibreOffice recalculation and rendered-sheet evidence are "
        "independent checks; they never silently replace source values.",
        "",
        "## Automated review summary",
        "",
        "| Worksheet | Range | Status | Severity | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for worksheet in document.worksheets:
        status = worksheet.review.status if worksheet.review else "REVIEW_REQUIRED"
        severity = worksheet.review.severity if worksheet.review else "MEDIUM"
        reasons = (
            "; ".join(
                finding.description for finding in worksheet.review.findings
            )
            if worksheet.review and worksheet.review.findings
            else "No discrepancy found."
        )
        lines.append(
            f"| {worksheet.worksheet_index}: {worksheet.title} | "
            f"{worksheet.used_range or 'unknown'} | **{status}** | "
            f"**{severity}** | {reasons.replace('|', '\\|').replace(chr(10), ' ')} |"
        )
    for worksheet in document.worksheets:
        lines.extend(_worksheet_section(worksheet))
    return "\n".join(lines).rstrip() + "\n"


def _worksheet_section(worksheet: NormalizedWorksheet) -> list[str]:
    lines = [
        "",
        "---",
        "",
        f"## Worksheet {worksheet.worksheet_index}: {worksheet.title}",
        "",
        f"- State: `{worksheet.state}`",
        f"- Used range: `{worksheet.used_range or 'unknown'}`",
        f"- Logical regions: {len(worksheet.regions)}",
        f"- Formulas: {sum(1 for cell in worksheet.cells if cell.formula)}",
        f"- Charts/images: {worksheet.chart_count}/{worksheet.image_count}",
        "",
    ]
    if worksheet.image_path:
        lines.extend(
            [
                f"![Rendered worksheet {worksheet.worksheet_index}]({worksheet.image_path})",
                "",
            ]
        )
    lines.extend(
        [
            "### Native worksheet extraction",
            "",
            worksheet.native_markdown or "_(No native worksheet text.)_",
            "",
            "### Formula audit",
            "",
        ]
    )
    formula_cells = [cell for cell in worksheet.cells if cell.formula]
    if not formula_cells:
        lines.append("- No formulas.")
    else:
        for cell in formula_cells:
            lines.append(
                f"- `{cell.coordinate}` `{cell.formula}`: "
                f"status **{cell.formula_status}**; cached `{cell.cached_value}`; "
                f"recalculated `{cell.recalculated_value}`"
            )
    if worksheet.vision is not None:
        lines.extend(
            [
                "",
                "### Independent visual extraction",
                "",
                worksheet.vision.transcription_markdown
                or "_(No visual transcription.)_",
                "",
                worksheet.vision.visual_description
                or "_(No visual description.)_",
            ]
        )
    lines.extend(["", "### Review decision", ""])
    if worksheet.review is None:
        lines.append("- REVIEW_REQUIRED: the independent judge did not complete.")
    else:
        lines.extend(
            [
                f"- Status: **{worksheet.review.status}**",
                f"- Severity: **{worksheet.review.severity}**",
            ]
        )
        for finding in worksheet.review.findings:
            lines.append(
                f"- `{finding.category}` ({finding.source}): {finding.description}"
            )
    questions = review_questions(worksheet)
    lines.extend(["", "### Human resolution questions", ""])
    if not questions:
        lines.append("- None. This worksheet passed automatically.")
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
            worksheet.combined_markdown or "_(Empty.)_",
            "",
            "### Provenance",
            "",
            f"- Render version: `{worksheet.render_version or 'not required'}`",
        ]
    )
    if worksheet.vision_provenance:
        lines.extend(
            [
                f"- Visual provider/model: `{worksheet.vision_provenance.provider}` / "
                f"`{worksheet.vision_provenance.model}`",
                f"- Visual prompt: `{worksheet.vision_provenance.prompt_version}`",
            ]
        )
    if worksheet.review and worksheet.review.judge_provenance:
        lines.extend(
            [
                f"- Judge provider/model: `{worksheet.review.judge_provenance.provider}` / "
                f"`{worksheet.review.judge_provenance.model}`",
                f"- Judge prompt: `{worksheet.review.judge_provenance.prompt_version}`",
            ]
        )
    return lines
