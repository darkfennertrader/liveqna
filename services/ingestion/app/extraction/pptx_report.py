from __future__ import annotations

from .models import NormalizedDocument, NormalizedSlide
from .pptx_resolution import review_questions


PPTX_LLM_REPORT_FILENAME = "PPTX-LLM-SLIDE-REPORT.md"
PPTX_REVIEW_REPORT_FILENAME = "PPTX-GATE-2-EXCEPTIONS.md"


def build_pptx_llm_report(document: NormalizedDocument) -> str:
    if document.source_format != "pptx":
        raise ValueError("PPTX LLM report requires a normalized PPTX document")
    if not document.slides:
        raise ValueError("PPTX LLM report requires visual slide results")

    lines = [
        "# PPTX LLM slide report",
        "",
        f"Source: `{document.source_filename}`",
        f"Content hash: `{document.content_hash}`",
        "",
        "This report shows the native extraction, multimodal-LLM output, and "
        "final reconciled content for each processed slide.",
        "",
        "## What to compare",
        "",
        "For every reviewed slide, perform these four comparisons:",
        "",
        "1. **Original PPTX slide vs rendered image:** confirm that the PNG "
        "faithfully shows the original slide without missing or displaced content.",
        "2. **Rendered image vs native extraction:** confirm that exact visible "
        "wording and tables are present. Compare speaker notes against the Notes "
        "pane in PowerPoint because notes are not visible in the PNG.",
        "3. **Rendered image vs LLM output:** confirm that transcription, tables, "
        "diagram relationships, chart meaning and labels are supported by the "
        "image and contain no invented information.",
        "4. **Native and LLM evidence vs final reconciled output:** confirm that "
        "native wording appears once, duplicate visual text is suppressed, and "
        "additional visual meaning remains present.",
        "",
        "Accept the PPTX only when every processed slide passes all four "
        "comparisons. Otherwise reject it and record the slide number and the "
        "incorrect section.",
        "",
        "## Automated review summary",
        "",
        "| Slide | Status | Severity | Category | Reason |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for slide in document.slides:
        status, severity, category, reason = _review_summary(slide)
        safe_reason = reason.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {slide.slide_number} | **{status}** | **{severity}** | "
            f"`{category}` | {safe_reason} |"
        )
    lines.append("")
    for slide in document.slides:
        lines.extend(_slide_section(slide))
    return "\n".join(lines).rstrip() + "\n"


def build_pptx_review_report(document: NormalizedDocument) -> str:
    if document.source_format != "pptx":
        raise ValueError("PPTX review report requires a normalized PPTX document")
    flagged = [
        slide
        for slide in document.slides
        if slide.review is None or slide.review.status == "REVIEW_REQUIRED"
    ]
    lines = [
        "# PPTX Gate 2 exception report",
        "",
        f"Source: `{document.source_filename}`",
        f"Content hash: `{document.content_hash}`",
        "",
        "Native PPTX extraction has precedence for exact text, numbers, and labels. "
        "This report contains only slides requiring human review; slides that "
        "automatically passed are omitted.",
        "",
        "## Deterministic severity rule",
        "",
        "| Category | Severity | Gate 2 result |",
        "| --- | --- | --- |",
        "| `ATOMIC_FACT_CONFLICT` | CRITICAL | Review required |",
        "| `INVENTED_RELATIONSHIP` | CRITICAL | Review required |",
        "| `MISSING_KEY_ELEMENT` | HIGH | Review required |",
        "| `PARTIAL_OR_UNCERTAIN` | MEDIUM | Review required |",
        "| `FORMAT_ONLY` | LOW | Automatic pass |",
        "| `NO_DISCREPANCY` | NONE | Automatic pass |",
        "",
        f"Slides evaluated: **{len(document.slides)}**  ",
        f"Slides requiring review: **{len(flagged)}**",
        "",
    ]
    if not flagged:
        lines.extend(
            [
                "## Review queue",
                "",
                "No slide was placed in the human-review queue.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## Review queue",
            "",
            "| Slide | Severity | Finding | Source |",
            "| ---: | --- | --- | --- |",
        ]
    )
    for slide in flagged:
        if slide.review is None:
            lines.append(
                f"| {slide.slide_number} | MEDIUM | Independent review was not run. | system |"
            )
            continue
        for finding in slide.review.findings:
            description = finding.description.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {slide.slide_number} | **{finding.severity}** | "
                f"{description} | `{finding.source}` |"
            )

    for slide in flagged:
        lines.extend(_review_slide_section(slide))
    return "\n".join(lines).rstrip() + "\n"


def _slide_section(slide: NormalizedSlide) -> list[str]:
    title = slide.title or "Untitled"
    lines = [
        f"## Slide {slide.slide_number}: {title}",
        "",
        f"![Rendered slide {slide.slide_number}]({slide.image_path})",
        "",
        "### Native extraction",
        "",
        slide.native_markdown or "_(No native visible text.)_",
        "",
        "### LLM transcription",
        "",
        slide.vision.transcription_markdown
        or "_(No visual transcription returned.)_",
        "",
        "### LLM visual description",
        "",
        slide.vision.visual_description
        or "_(No visual description returned.)_",
        "",
    ]
    if slide.vision.tables_markdown:
        lines.extend(
            [
                "### LLM visual tables",
                "",
                "\n\n".join(slide.vision.tables_markdown),
                "",
            ]
        )
    lines.extend(
        [
            "### LLM labels",
            "",
            ", ".join(slide.vision.labels) or "_(None.)_",
            "",
            "### Discrepancies and uncertainties",
            "",
        ]
    )
    if slide.vision.discrepancies:
        lines.extend(
            "- "
            + ("MATERIAL" if item.material else "Non-material")
            + f": {item.description}"
            for item in slide.vision.discrepancies
        )
    if slide.vision.uncertainties:
        lines.extend(
            f"- Uncertainty: {uncertainty}"
            for uncertainty in slide.vision.uncertainties
        )
    if not slide.vision.discrepancies and not slide.vision.uncertainties:
        lines.append("- None reported by the model.")
    lines.extend(["", "### Evidence reconciliation", ""])
    if slide.reconciliation is None:
        lines.append("_(No element-level reconciliation record.)_")
    else:
        lines.extend(
            [
                f"- Strategy: `{slide.reconciliation.version}`",
                f"- Native elements: {len(slide.native_elements)}",
                "- Visual elements returned directly: "
                + str(len(slide.vision.elements)),
                "- Suppressed duplicate visual evidence: "
                + (
                    ", ".join(
                        slide.reconciliation.suppressed_duplicate_visual_ids
                    )
                    or "none"
                ),
                "- Included complementary visual evidence: "
                + (", ".join(slide.reconciliation.included_visual_ids) or "none"),
                "- Conflicts requiring review: "
                + ("; ".join(slide.reconciliation.conflicts) or "none"),
            ]
        )
    lines.extend(["", "### Logical slide relationships", ""])
    if slide.relationships:
        for relationship in slide.relationships:
            signal_summary = ", ".join(
                f"{signal.name}={signal.value:.3f}×{signal.weight:.2f}"
                for signal in relationship.signals
            )
            lines.append(
                f"- `{relationship.type}` → slide {relationship.target_slide}; "
                f"confidence={relationship.confidence:.3f}; "
                f"method=`{relationship.method}`; "
                f"linker=`{relationship.linker_version}`; {relationship.evidence} "
                f"Signals: {signal_summary or 'none'}."
            )
    else:
        lines.append("- No cross-slide relationship met the configured threshold.")
    lines.extend(["", "### Independent review decision", ""])
    if slide.review is None:
        lines.extend(
            [
                "- Status: **HUMAN REVIEW REQUIRED**",
                "- Severity: **MEDIUM**",
                "- Category: `PARTIAL_OR_UNCERTAIN`",
                "- Reason: Independent second-pass review was not run.",
            ]
        )
    else:
        lines.extend(
            [
                f"- Status: **{slide.review.status}**",
                f"- Severity: **{slide.review.severity}**",
            ]
        )
        if slide.review.findings:
            for finding in slide.review.findings:
                lines.extend(
                    [
                        f"- Category: `{finding.category}`",
                        f"- Reason: {finding.description}",
                        f"- Evidence: {finding.evidence}",
                        f"- Finding source: `{finding.source}`",
                    ]
                )
        elif slide.review.judge_result is not None:
            for finding in slide.review.judge_result.findings:
                lines.extend(
                    [
                        f"- Category: `{finding.category}`",
                        f"- Reason: {finding.description}",
                        f"- Evidence: {finding.evidence}",
                        "- Finding source: `judge`",
                    ]
                )
        else:
            lines.extend(
                [
                    "- Category: `PARTIAL_OR_UNCERTAIN`",
                    "- Reason: No independent judge result is available.",
                ]
            )
    questions = review_questions(slide)
    if questions:
        lines.extend(["", "### Human discrepancy resolution", ""])
        for question in questions:
            resolution = question["resolution"]
            lines.extend(
                [
                    f"#### Question {question['finding_index'] + 1}",
                    "",
                    f"- Severity: **{question['severity']}**",
                    f"- Discrepancy: {question['description']}",
                    f"- Question: **{question['question']}**",
                ]
            )
            if resolution is None:
                lines.append("- Resolution status: **UNANSWERED**")
            else:
                lines.extend(
                    [
                        f"- Human answer: {resolution['answer']}",
                        f"- Resolution status: **{resolution['status']}**",
                        f"- LLM rationale: {resolution['rationale']}",
                        "- Remaining uncertainty: "
                        + ("yes" if resolution["remaining_uncertainty"] else "no"),
                    ]
                )
    lines.extend(
        [
            "",
            "### Final reconciled output",
            "",
            slide.combined_markdown,
            "",
            "### Provenance",
            "",
            f"- Provider: `{slide.vision_provenance.provider}`",
            f"- Model: `{slide.vision_provenance.model}`",
            f"- Prompt: `{slide.vision_provenance.prompt_version}`",
            f"- Render version: `{slide.render_version}`",
            f"- Image hash: `{slide.image_hash}`",
            f"- Response hash: `{slide.vision_provenance.response_hash}`",
            f"- LLM duration: `{slide.vision_provenance.duration_ms} ms`",
            "",
            "### Human validation for this slide",
            "",
            "- [ ] Rendered image matches the original PPTX slide.",
            "- [ ] Native extraction matches the exact source wording and tables.",
            "- [ ] LLM output describes only evidence visible in the image.",
            "- [ ] Final output contains the native evidence once and retains "
            "additional visual meaning.",
            "",
            "***",
            "",
        ]
    )
    return lines


def _review_summary(slide: NormalizedSlide) -> tuple[str, str, str, str]:
    if slide.review is None:
        return (
            "HUMAN REVIEW REQUIRED",
            "MEDIUM",
            "PARTIAL_OR_UNCERTAIN",
            "Independent second-pass review was not run.",
        )
    questions = review_questions(slide)
    resolved = bool(questions) and all(
        question["resolution"] is not None
        and question["resolution"]["status"] == "APPLIED"
        for question in questions
    )
    status = (
        "HUMAN RESOLVED"
        if resolved
        else
        "HUMAN REVIEW REQUIRED"
        if slide.review.status == "REVIEW_REQUIRED"
        else "AUTO PASS"
    )
    if slide.review.findings:
        categories = ", ".join(
            dict.fromkeys(finding.category for finding in slide.review.findings)
        )
        reasons = "; ".join(
            dict.fromkeys(finding.description for finding in slide.review.findings)
        )
        return status, slide.review.severity, categories, reasons
    if slide.review.judge_result is not None:
        categories = ", ".join(
            dict.fromkeys(
                finding.category
                for finding in slide.review.judge_result.findings
            )
        )
        reasons = "; ".join(
            dict.fromkeys(
                finding.description
                for finding in slide.review.judge_result.findings
            )
        )
        return status, slide.review.severity, categories, reasons
    return status, slide.review.severity, "NO_DISCREPANCY", "No discrepancy found."


def _review_slide_section(slide: NormalizedSlide) -> list[str]:
    title = slide.title or "Untitled"
    lines = [
        "",
        f"## Slide {slide.slide_number}: {title}",
        "",
        f"![Rendered slide {slide.slide_number}]({slide.image_path})",
        "",
        "### Findings",
        "",
    ]
    if slide.review is None:
        lines.append("- Independent review was not run.")
    else:
        for finding in slide.review.findings:
            lines.extend(
                [
                    f"- **{finding.severity} — {finding.category}** "
                    f"(`{finding.source}`): {finding.description}",
                    f"  - Evidence: {finding.evidence}",
                ]
            )
        if slide.review.judge_provenance is not None:
            provenance = slide.review.judge_provenance
            lines.extend(
                [
                    "",
                    "### Judge provenance",
                    "",
                    f"- Provider: `{provenance.provider}`",
                    f"- Model: `{provenance.model}`",
                    f"- Prompt: `{provenance.prompt_version}`",
                    f"- Duration: `{provenance.duration_ms} ms`",
                    f"- Response hash: `{provenance.response_hash}`",
                ]
            )
    lines.extend(
        [
            "",
            "### Human decision",
            "",
            "- [ ] ACCEPT: finding is resolved or not material.",
            "- [ ] REJECT: correct the extraction/reconciliation before retrieval.",
            "- Reviewer note:",
            "",
            "---",
        ]
    )
    return lines
