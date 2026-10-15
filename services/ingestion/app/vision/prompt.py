from __future__ import annotations

import json

from ..extraction.models import SlideReconciliation, SlideVisionResult

PROMPT_VERSION = "pptx-slide-v4"
PDF_PROMPT_VERSION = "pdf-page-v1"
REVIEW_PROMPT_VERSION = "pptx-slide-review-v1"


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
retrieval labels. Native PDF text is authoritative for exact machine-readable
wording. Use the image to add layout and visual relationships, not to replace or
paraphrase native evidence. Report material discrepancies and uncertainty rather
than guessing. Never reconstruct text that is covered, clipped, blurred, or only
partially visible. Do not infer speaker attribution or timestamps that are not
explicitly present on the page."""


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


def page_prompt(
    *, page_number: int, content_profile: str, native_markdown: str
) -> str:
    return (
        f"PDF page number: {page_number}\n"
        f"Business content profile: {content_profile}\n\n"
        "Native PDF extraction (authoritative for exact machine-readable wording):\n"
        f"{native_markdown or '[no native text extracted]'}\n\n"
        "Analyze the rendered page and produce the structured extraction."
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
