from __future__ import annotations

import json

from ..extraction.models import (
    SlideReconciliation,
    SlideReviewDecisionFinding,
    SlideVisionResult,
)

PROMPT_VERSION = "pptx-slide-v4"
PDF_PROMPT_VERSION = "pdf-page-v2"
PDF_REVIEW_PROMPT_VERSION = "pdf-page-review-v1"
PDF_RESOLUTION_PROMPT_VERSION = "pdf-human-resolution-v1"
DOCX_PROMPT_VERSION = "docx-page-v1"
DOCX_REVIEW_PROMPT_VERSION = "docx-page-review-v1"
DOCX_RESOLUTION_PROMPT_VERSION = "docx-human-resolution-v1"
REVIEW_PROMPT_VERSION = "pptx-slide-review-v1"
RESOLUTION_PROMPT_VERSION = "pptx-human-resolution-v1"
XLSX_PROMPT_VERSION = "xlsx-worksheet-v1"
XLSX_REVIEW_PROMPT_VERSION = "xlsx-worksheet-review-v1"
XLSX_RESOLUTION_PROMPT_VERSION = "xlsx-human-resolution-v1"


SYSTEM_INSTRUCTIONS = """You extract retrieval evidence from one presentation slide.
Return only the requested structured result. Treat the slide image as evidence.
Transcribe visible wording faithfully into Markdown, preserve table structure as
Markdown tables, describe charts and diagrams concisely, and provide specific
retrieval labels. Also return region-level elements in visual reading order.
For each element classify its kind, preserve its content or concise visual
meaning, and return its bounding box in normalized 0-1000 slide coordinates;
use null only when the region cannot be localized. Do not infer facts that are
not visible. This is an independent visual evidence pass: no native extraction
is supplied. Leave discrepancies empty unless the image itself contains visibly
conflicting statements. Put uncertainty in the uncertainties field instead of
guessing. Never reconstruct text that is covered, clipped, blurred, or only
partially visible. State that the element is obscured and omit the guessed
wording. A template overlay such as "Example" may cover diagram components;
describe the visible relationship without inventing the hidden component name."""


PDF_SYSTEM_INSTRUCTIONS = """You extract retrieval evidence from one rendered PDF page.
Return only the requested structured result. Treat the page image as evidence.
Transcribe visible wording faithfully into Markdown, preserve table structure as
Markdown tables, describe charts and diagrams concisely, and provide specific
retrieval labels. Also return region-level elements in visual reading order with
normalized 0-1000 page coordinates. This is an independent visual evidence pass:
no native extraction is supplied. Do not infer facts that are not visible. Put
uncertainty in the uncertainties field instead of guessing. Never reconstruct
text that is covered, clipped, blurred, or only partially visible. Do not infer
speaker attribution or timestamps that are not explicitly present on the page."""


PDF_REVIEW_SYSTEM_INSTRUCTIONS = """You are the independent second-pass reviewer
for one PDF page. Compare the rendered image with the supplied native PDF layout
extraction, first-pass visual extraction, and deterministic reconciliation.
Native PDF extraction is authoritative for exact machine-readable wording,
numbers, dates, names, and labels. Visual evidence may add layout, chart or
diagram meaning, and relationships, but may never replace native text. Use the
categories ATOMIC_FACT_CONFLICT, INVENTED_RELATIONSHIP, MISSING_KEY_ELEMENT,
PARTIAL_OR_UNCERTAIN, FORMAT_ONLY, and NO_DISCREPANCY. Return one NO_DISCREPANCY
finding when no issue exists, and never combine it with another category.
Return only the requested structured result."""


PDF_RESOLUTION_SYSTEM_INSTRUCTIONS = """You resolve one PDF-page review finding
using an explicit human answer. Return a complete proposed normalized Markdown
representation of the page. Preserve authoritative native wording, numbers,
dates, names, and labels unless the human explicitly supplies a correction. Do
not introduce facts beyond the native evidence, rendered page, current normalized
Markdown, finding, and human answer. If the answer is insufficient, set
remaining_uncertainty to true and ask one precise follow-up question. Return only
the requested structured result."""


DOCX_SYSTEM_INSTRUCTIONS = """You extract retrieval evidence from one rendered
Word-document page. Return only the requested structured result. Treat the page
image as independent evidence: no OOXML extraction is supplied. Transcribe
visible wording faithfully, preserve tables as Markdown, describe diagrams,
charts, images, text boxes, callouts, and layout relationships, and return
region-level elements in reading order with normalized 0-1000 page coordinates.
Do not infer wording hidden by clipping, overlap, tracked-change balloons, or
illegible rendering. Put uncertainty in the uncertainties field instead of
guessing."""


DOCX_REVIEW_SYSTEM_INSTRUCTIONS = """You are the independent second-pass
reviewer for one rendered Word-document page. Compare the image with the
supplied authoritative OOXML extraction, first-pass visual extraction, and
deterministic reconciliation. OOXML text, table cells, links, and revision
policy are authoritative for exact facts. Visual evidence may add diagram,
image, layout, and relationship meaning but may never silently replace native
wording. Report only evidence-supported issues using ATOMIC_FACT_CONFLICT,
INVENTED_RELATIONSHIP, MISSING_KEY_ELEMENT, PARTIAL_OR_UNCERTAIN, FORMAT_ONLY,
or NO_DISCREPANCY. Return exactly one NO_DISCREPANCY finding when no issue exists
and never combine it with another category. Return only the requested structured
result."""


DOCX_RESOLUTION_SYSTEM_INSTRUCTIONS = """You resolve one Word-page review
finding using an explicit human answer. Return a complete proposed normalized
Markdown representation of the page. Preserve authoritative OOXML wording,
numbers, links, table values, and the configured revision view unless the human
explicitly supplies a correction. Do not introduce facts beyond the evidence and
human answer. If the answer is insufficient, set remaining_uncertainty to true
and ask one precise follow-up question. Return only the requested structured
result."""


REVIEW_SYSTEM_INSTRUCTIONS = """You are the independent second-pass reviewer for one
presentation slide. Compare the rendered image with the supplied native PPTX
extraction, first-pass visual extraction, and deterministic reconciliation.
Native PPTX extraction is authoritative for exact machine-readable wording,
numbers, and labels; visual evidence may add layout, chart or diagram meaning,
and relationships, but may never replace native text. Do not trust a prior
output merely because it is supplied. Report only discrepancies supported by
the evidence. Use exactly these categories:
ATOMIC_FACT_CONFLICT for conflicting numbers, dates, names, or labels;
INVENTED_RELATIONSHIP for an unsupported connection or causal/flow claim;
MISSING_KEY_ELEMENT for an important visible element omitted from the final
reconciled result; PARTIAL_OR_UNCERTAIN when evidence is clipped, obscured, or
cannot be judged confidently; FORMAT_ONLY for a non-semantic presentation issue;
and NO_DISCREPANCY when no review issue exists. If there is no issue, return
exactly one NO_DISCREPANCY finding. Never combine NO_DISCREPANCY with another
category. Return only the requested structured result."""


RESOLUTION_SYSTEM_INSTRUCTIONS = """You resolve one presentation-slide review
finding using an explicit human answer. Return a complete proposed normalized
Markdown representation of the slide. Preserve authoritative native wording,
numbers, dates, names, and labels unless the human explicitly supplies a
correction. You may add meaning stated by the human, but identify it naturally
without claiming it was machine-extracted from the slide. Do not introduce any
fact beyond the native evidence, rendered image, current normalized Markdown,
finding, and human answer. If the answer is ambiguous, unknown, or insufficient,
set remaining_uncertainty to true and ask one precise follow-up question. Never
silently discard source evidence. Return only the requested structured result."""


XLSX_SYSTEM_INSTRUCTIONS = """You extract visual retrieval evidence from one
rendered spreadsheet worksheet. Return only the requested structured result.
Treat the rendered worksheet as independent visual evidence: no native cells,
formulas, or table definitions are supplied. Transcribe visible labels faithfully,
preserve small visible tables as Markdown, and describe charts, dashboards,
conditional formatting, diagrams, instructions, and layout relationships. Return
region-level elements in visual reading order with normalized 0-1000 coordinates.
Do not calculate, reconstruct, or guess cell values that are clipped or illegible.
Do not infer formula semantics or external data. Put uncertainty in the
uncertainties field instead of guessing."""


XLSX_REVIEW_SYSTEM_INSTRUCTIONS = """You are the independent second-pass reviewer
for one spreadsheet worksheet. Compare the rendered worksheet with the supplied
native cell/table/formula extraction, first-pass visual extraction, and
deterministic reconciliation. Native cell values, formulas, coordinates, table
ranges, and calculated-value audit are authoritative for exact facts. Visual
evidence may add chart, dashboard, formatting, and layout meaning but may never
replace native numeric or textual cell evidence. Report only evidence-supported
issues using ATOMIC_FACT_CONFLICT, INVENTED_RELATIONSHIP, MISSING_KEY_ELEMENT,
PARTIAL_OR_UNCERTAIN, FORMAT_ONLY, or NO_DISCREPANCY. Return exactly one
NO_DISCREPANCY finding when no issue exists and never combine it with another
category. Return only the requested structured result."""


XLSX_RESOLUTION_SYSTEM_INSTRUCTIONS = """You resolve one spreadsheet worksheet
review finding using an explicit human answer. Return a complete proposed
normalized Markdown representation of the worksheet. Preserve authoritative
native cell wording, numbers, coordinates, table boundaries, and formulas unless
the human explicitly supplies a correction. Never silently replace native values
with a visual or recalculated estimate. If the answer is insufficient, set
remaining_uncertainty to true and ask one precise follow-up question. Return only
the requested structured result."""


def slide_prompt(
    *,
    slide_number: int,
    content_profile: str,
    native_markdown: str,
    native_elements: list[dict],
) -> str:
    del native_markdown, native_elements
    return (
        f"Slide number: {slide_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Analyze only the rendered slide and produce the structured extraction. "
        "Do not assume access to the PPTX object model or hidden speaker notes."
    )


def page_prompt(*, page_number: int, content_profile: str) -> str:
    return (
        f"PDF page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Analyze only the rendered page and produce the structured extraction. "
        "Do not assume access to the PDF object model or hidden metadata."
    )


def word_page_prompt(*, page_number: int, content_profile: str) -> str:
    return (
        f"Rendered Word page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Analyze only the rendered page and produce the structured extraction. "
        "Do not assume access to OOXML, comments, revisions, or hidden metadata."
    )


def worksheet_prompt(
    *, worksheet_index: int, worksheet_title: str, content_profile: str
) -> str:
    return (
        f"Worksheet index: {worksheet_index}\n"
        f"Worksheet title: {worksheet_title}\n"
        f"Business content profile: {content_profile}\n\n"
        "Analyze only the rendered worksheet and produce the structured "
        "extraction. Do not assume access to workbook cells, formulas, hidden "
        "worksheets, or external links."
    )


def worksheet_review_prompt(
    *,
    worksheet_index: int,
    worksheet_title: str,
    content_profile: str,
    native_markdown: str,
    native_elements: list[dict],
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
) -> str:
    evidence = {
        "native_markdown": native_markdown,
        "native_elements": native_elements,
        "first_pass_visual_extraction": vision.model_dump(mode="json"),
        "deterministic_reconciliation": reconciliation.model_dump(mode="json"),
    }
    return (
        f"Worksheet index: {worksheet_index}\n"
        f"Worksheet title: {worksheet_title}\n"
        f"Business content profile: {content_profile}\n\n"
        "Review the rendered worksheet against this evidence bundle. Native "
        "cell evidence remains authoritative for exact facts.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def worksheet_resolution_prompt(
    *,
    worksheet_index: int,
    worksheet_title: str,
    content_profile: str,
    native_markdown: str,
    current_markdown: str,
    finding: SlideReviewDecisionFinding,
    question: str,
    answer: str,
) -> str:
    evidence = {
        "native_markdown": native_markdown,
        "current_normalized_markdown": current_markdown,
        "review_finding": finding.model_dump(mode="json"),
        "question": question,
        "human_answer": answer,
    }
    return (
        f"Worksheet index: {worksheet_index}\n"
        f"Worksheet title: {worksheet_title}\n"
        f"Business content profile: {content_profile}\n\n"
        "Resolve only the supplied finding. The human answer is authoritative "
        "for the requested clarification; unchanged native cells remain "
        "authoritative.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def page_review_prompt(
    *,
    page_number: int,
    content_profile: str,
    native_markdown: str,
    native_elements: list[dict],
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
) -> str:
    evidence = {
        "native_markdown": native_markdown,
        "native_elements": native_elements,
        "first_pass_visual_extraction": vision.model_dump(mode="json"),
        "deterministic_reconciliation": reconciliation.model_dump(mode="json"),
    }
    return (
        f"PDF page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Review the rendered page against this evidence bundle. The rendered "
        "page and native extraction are source evidence; generated descriptions "
        "are claims to verify.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def page_resolution_prompt(
    *,
    page_number: int,
    content_profile: str,
    native_markdown: str,
    current_markdown: str,
    finding: SlideReviewDecisionFinding,
    question: str,
    answer: str,
) -> str:
    evidence = {
        "native_markdown": native_markdown,
        "current_normalized_markdown": current_markdown,
        "review_finding": finding.model_dump(mode="json"),
        "question": question,
        "human_answer": answer,
    }
    return (
        f"PDF page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Resolve only the supplied finding. The human answer is authoritative "
        "for the requested clarification, while native extraction remains "
        "authoritative for unchanged exact source facts.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def word_page_review_prompt(
    *,
    page_number: int,
    content_profile: str,
    native_markdown: str,
    native_elements: list[dict],
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
) -> str:
    evidence = {
        "authoritative_ooxml_markdown": native_markdown,
        "authoritative_ooxml_elements": native_elements,
        "first_pass_visual_extraction": vision.model_dump(mode="json"),
        "deterministic_reconciliation": reconciliation.model_dump(mode="json"),
    }
    return (
        f"Rendered Word page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Review the rendered Word page against this evidence bundle. Native "
        "OOXML remains authoritative for exact source facts.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def word_page_resolution_prompt(
    *,
    page_number: int,
    content_profile: str,
    native_markdown: str,
    current_markdown: str,
    finding: SlideReviewDecisionFinding,
    question: str,
    answer: str,
) -> str:
    evidence = {
        "authoritative_ooxml_markdown": native_markdown,
        "current_normalized_markdown": current_markdown,
        "review_finding": finding.model_dump(mode="json"),
        "question": question,
        "human_answer": answer,
    }
    return (
        f"Rendered Word page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Resolve only the supplied finding. The human answer is authoritative "
        "for the clarification; unchanged OOXML evidence remains authoritative.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def review_prompt(
    *,
    slide_number: int,
    content_profile: str,
    native_markdown: str,
    native_elements: list[dict],
    vision: SlideVisionResult,
    reconciliation: SlideReconciliation,
) -> str:
    evidence = {
        "native_markdown": native_markdown,
        "native_elements": native_elements,
        "first_pass_visual_extraction": vision.model_dump(mode="json"),
        "deterministic_reconciliation": reconciliation.model_dump(mode="json"),
    }
    return (
        f"Slide number: {slide_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Review the rendered slide against this evidence bundle. The rendered "
        "image and native extraction are source evidence; prior generated "
        "descriptions are claims to verify.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def resolution_prompt(
    *,
    slide_number: int,
    content_profile: str,
    native_markdown: str,
    current_markdown: str,
    finding: SlideReviewDecisionFinding,
    question: str,
    answer: str,
) -> str:
    evidence = {
        "native_markdown": native_markdown,
        "current_normalized_markdown": current_markdown,
        "review_finding": finding.model_dump(mode="json"),
        "question": question,
        "human_answer": answer,
    }
    return (
        f"Slide number: {slide_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Resolve only the supplied finding. The human answer is authoritative "
        "for the requested clarification, while native extraction remains "
        "authoritative for unchanged exact source facts.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )
