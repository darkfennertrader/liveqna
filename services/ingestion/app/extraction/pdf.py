from __future__ import annotations

import hashlib
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from ..vision.base import SlideReviewProvider, VisionProvider, VisionProviderError
from ..vision.prompt import PDF_PROMPT_VERSION, PDF_REVIEW_PROMPT_VERSION
from .common import DocumentBuilder, clean_text
from .errors import ExtractionFailure
from .models import (
    ExtractionQuality,
    NormalizedDocument,
    NormalizedPage,
    PageNativeElement,
    SlideBoundingBox,
    SlideReviewProvenance,
    SlideVisionProvenance,
    SourceLocator,
    TranscriptMetadata,
)
from .pdf_link import link_pages
from .pdf_renderer import PdfRenderer, RenderedPage
from .pptx_reconcile import reconcile_page
from .pptx_review import decide_page_review, failed_page_review


TRANSCRIPT_VIDEO = re.compile(
    r"^Video\s+\d+\s*:\s*(?P<name>.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
TRANSCRIPT_TIME_RANGE = re.compile(
    r"Time\s+Range\s*:\s*(?P<start>\d{1,2}:\d{2}:\d{2})\s+to\s+"
    r"(?P<end>\d{1,2}:\d{2}:\d{2})"
    r"(?:\s+Duration\s*:\s*(?P<duration>[0-9]+(?:\.[0-9]+)?)\s+seconds)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NativePdfPage:
    page_number: int
    block_ids: list[str]
    native_markdown: str
    native_elements: list[PageNativeElement]
    text_character_count: int
    max_image_pixels: int
    vector_operation_count: int


def extract_pdf(
    path: Path,
    source_filename: str,
    content_hash: str,
    *,
    document_type: str = "official",
    content_profile: str = "general_document",
    renderer: PdfRenderer | None = None,
    vision_provider: VisionProvider | None = None,
    review_provider: SlideReviewProvider | None = None,
    vision_prompt_version: str = PDF_PROMPT_VERSION,
    review_prompt_version: str = PDF_REVIEW_PROMPT_VERSION,
    max_concurrency: int = 4,
) -> NormalizedDocument:
    try:
        document = pymupdf.open(path)
        if document.needs_pass:
            document.close()
            raise ExtractionFailure(
                "ENCRYPTED_DOCUMENT", "encrypted PDFs are not supported"
            )
        builder = DocumentBuilder(source_filename, "pdf", content_hash)
        pages, pages_requiring_ocr, native_warnings = _extract_native_pages(
            document, builder
        )
        page_count = document.page_count
        document.close()
    except ExtractionFailure:
        raise
    except Exception as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "PDF file could not be parsed"
        ) from exc

    native_document = _build_native_document(builder)
    if native_warnings:
        native_document = _with_quality_warnings(native_document, native_warnings)

    if document_type == "transcript":
        return _extract_transcript_document(
            native_document, pages, pages_requiring_ocr
        )

    # OCR is intentionally outside this phase. Preserve partial native evidence
    # and route the document to review instead of inventing page content.
    if pages_requiring_ocr:
        partial = _with_quality_warnings(
            native_document,
            [
                "pages requiring OCR (not implemented): "
                + ", ".join(str(number) for number in pages_requiring_ocr)
            ],
        )
        raise ExtractionFailure(
            "OCR_REQUIRED",
            "PDF contains pages without native text; OCR is not implemented: "
            + ", ".join(str(number) for number in pages_requiring_ocr),
            partial_document=partial,
        )

    if renderer is None:
        raise ExtractionFailure(
            "PDF_RENDERER_REQUIRED",
            "PDF evidence extraction requires a configured page renderer",
            partial_document=native_document,
        )
    if vision_provider is None:
        raise ExtractionFailure(
            "PDF_VISION_PROVIDER_REQUIRED",
            "PDF evidence extraction requires a configured vision provider",
            partial_document=native_document,
        )
    if review_provider is None:
        raise ExtractionFailure(
            "PDF_REVIEW_PROVIDER_REQUIRED",
            "PDF evidence extraction requires an independent review provider",
            partial_document=native_document,
        )

    try:
        rendered = renderer.render(
            path, page_count, [page.page_number for page in pages]
        )
    except ExtractionFailure as exc:
        raise ExtractionFailure(
            exc.code, str(exc), partial_document=native_document
        ) from exc
    rendered_by_number = {item.page_number: item for item in rendered}
    missing = [
        page.page_number
        for page in pages
        if page.page_number not in rendered_by_number
    ]
    if missing:
        raise ExtractionFailure(
            "PDF_RENDER_FAILED",
            "renderer omitted pages: " + ", ".join(map(str, missing)),
            partial_document=native_document,
        )

    completed: dict[int, NormalizedPage] = {}
    failures: list[str] = []
    workers = max(1, min(max_concurrency, len(pages)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _enrich_page,
                native,
                rendered_by_number[native.page_number],
                content_profile,
                vision_provider,
                review_provider,
                vision_prompt_version,
                review_prompt_version,
                renderer.version,
            ): native.page_number
            for native in pages
        }
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                completed[page_number] = future.result()
            except (VisionProviderError, OSError, ValueError) as exc:
                failures.append(
                    f"page {page_number}: {type(exc).__name__}: {exc}"
                )

    normalized_pages = [completed[number] for number in sorted(completed)]
    warnings = list(native_document.quality.warnings)
    for page in normalized_pages:
        warnings.extend(
            f"page {page.page_number} uncertainty: {item}"
            for item in page.vision.uncertainties
        )
    normalized = _with_pages(native_document, normalized_pages, warnings)
    if failures:
        raise ExtractionFailure(
            "PDF_VISION_FAILED",
            "PDF page processing failed for " + "; ".join(sorted(failures)),
            partial_document=normalized,
        )

    material = list(
        dict.fromkeys(
            f"page {page.page_number} [{finding.severity}]: {finding.description}"
            for page in normalized.pages
            if page.review is not None
            and page.review.status == "REVIEW_REQUIRED"
            for finding in page.review.findings
        )
    )
    if material:
        raise ExtractionFailure(
            "PDF_REVIEW_REQUIRED",
            "material native/visual discrepancies require review: "
            + "; ".join(material),
            partial_document=normalized,
        )
    return normalized


def _extract_native_pages(
    document: pymupdf.Document, builder: DocumentBuilder
) -> tuple[list[NativePdfPage], list[int], list[str]]:
    pages: list[NativePdfPage] = []
    pages_requiring_ocr: list[int] = []
    warnings: list[str] = []
    table_number = 0

    for page_number, page in enumerate(document, start=1):
        before = len(builder.blocks)
        elements: list[PageNativeElement] = []
        try:
            raw = page.get_text("dict", sort=True)
            text_lines = _text_lines(raw)
            tables = list(page.find_tables().tables)
            drawings = page.get_drawings()
        except Exception as exc:
            raise ExtractionFailure(
                "PDF_NATIVE_EXTRACTION_FAILED",
                f"native layout extraction failed for page {page_number}: "
                f"{type(exc).__name__}",
                partial_document=_build_native_document(builder),
            ) from exc

        table_boxes = [tuple(table.bbox) for table in tables]
        body_sizes = [line["font_size"] for line in text_lines if line["text"]]
        median_size = statistics.median(body_sizes) if body_sizes else 0.0
        candidates: list[dict[str, Any]] = []

        for table in tables:
            markdown = _table_markdown(table.extract())
            if markdown:
                candidates.append(
                    {
                        "kind": "table",
                        "content": markdown,
                        "bbox": tuple(table.bbox),
                        "attributes": {
                            "rows": len(table.rows),
                            "columns": (
                                len(table.rows[0].cells) if table.rows else 0
                            ),
                        },
                    }
                )

        for line in text_lines:
            if any(_contained(line["bbox"], box) for box in table_boxes):
                continue
            heading = bool(
                median_size
                and line["font_size"] >= median_size * 1.22
                and len(line["text"]) <= 180
            )
            candidates.append(
                {
                    "kind": "title" if heading else "text",
                    "content": line["text"],
                    "bbox": line["bbox"],
                    "attributes": {
                        "font": line["font"],
                        "font_size": round(line["font_size"], 2),
                    },
                }
            )

        candidates.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
        for reading_order, candidate in enumerate(candidates, start=1):
            element_before = len(builder.blocks)
            if candidate["kind"] == "table":
                table_number += 1
                builder.add(
                    "table",
                    candidate["content"],
                    SourceLocator(page=page_number, table=table_number),
                )
            elif candidate["kind"] == "title":
                builder.heading(
                    candidate["content"],
                    SourceLocator(page=page_number),
                    level=1,
                )
            else:
                builder.add(
                    "paragraph",
                    candidate["content"],
                    SourceLocator(page=page_number),
                )
            elements.append(
                PageNativeElement(
                    element_id=(
                        f"page-{page_number:04d}-element-{reading_order:04d}"
                    ),
                    kind=candidate["kind"],
                    content=(
                        "# " + candidate["content"]
                        if candidate["kind"] == "title"
                        else candidate["content"]
                    ),
                    bbox=_normalized_bbox(candidate["bbox"], page.rect),
                    block_ids=[
                        block.id for block in builder.blocks[element_before:]
                    ],
                    reading_order=reading_order,
                    attributes=candidate["attributes"],
                )
            )

        image_blocks = _image_blocks(raw)
        element_order = len(elements)
        for image_index, image in enumerate(image_blocks, start=1):
            element_order += 1
            elements.append(
                PageNativeElement(
                    element_id=(
                        f"page-{page_number:04d}-image-{image_index:04d}"
                    ),
                    kind="image",
                    content="",
                    bbox=_normalized_bbox(tuple(image["bbox"]), page.rect),
                    reading_order=element_order,
                    attributes={
                        "width": image.get("width", 0),
                        "height": image.get("height", 0),
                    },
                )
            )
        drawing_boxes = [
            tuple(item["rect"])
            for item in drawings
            if item.get("rect") is not None
        ]
        drawing_bbox = _union_bbox(drawing_boxes)
        if drawing_bbox is not None:
            element_order += 1
            elements.append(
                PageNativeElement(
                    element_id=f"page-{page_number:04d}-vectors",
                    kind="diagram",
                    content="",
                    bbox=_normalized_bbox(drawing_bbox, page.rect),
                    reading_order=element_order,
                    attributes={"drawing_path_count": len(drawings)},
                )
            )

        page_blocks = list(builder.blocks[before:])
        native_markdown = _blocks_markdown(page_blocks)
        character_count = len(clean_text(page.get_text("text")))
        if character_count == 0:
            pages_requiring_ocr.append(page_number)
        if not candidates and character_count:
            warnings.append(
                f"page {page_number} contains text but no positioned elements"
            )
        pages.append(
            NativePdfPage(
                page_number=page_number,
                block_ids=[block.id for block in page_blocks],
                native_markdown=native_markdown,
                native_elements=elements,
                text_character_count=character_count,
                max_image_pixels=max(
                    (
                        int(image.get("width", 0))
                        * int(image.get("height", 0))
                        for image in image_blocks
                    ),
                    default=0,
                ),
                vector_operation_count=len(drawings),
            )
        )
    return pages, pages_requiring_ocr, warnings


def _text_lines(raw: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = clean_text("".join(span.get("text", "") for span in spans))
            if not text:
                continue
            sizes = [
                float(span.get("size", 0))
                for span in spans
                if span.get("text")
            ]
            fonts = [
                str(span.get("font", ""))
                for span in spans
                if span.get("font")
            ]
            lines.append(
                {
                    "text": text,
                    "bbox": tuple(line.get("bbox", block.get("bbox"))),
                    "font_size": max(sizes, default=0.0),
                    "font": fonts[0] if fonts else "",
                }
            )
    return lines


def _image_blocks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in raw.get("blocks", []) if block.get("type") == 1]


def _normalized_bbox(
    bbox: tuple[float, float, float, float], page_rect: pymupdf.Rect
) -> SlideBoundingBox:
    x0, y0, x1, y1 = bbox

    def scale(value: float, total: float) -> int:
        return min(1000, max(0, round(value / total * 1000)))

    return SlideBoundingBox(
        x=scale(x0, page_rect.width),
        y=scale(y0, page_rect.height),
        width=scale(max(0.0, x1 - x0), page_rect.width),
        height=scale(max(0.0, y1 - y0), page_rect.height),
    )


def _contained(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> bool:
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    return (
        ix0 >= ox0 - 2
        and iy0 >= oy0 - 2
        and ix1 <= ox1 + 2
        and iy1 <= oy1 + 2
    )


def _union_bbox(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _table_markdown(rows: list[list[str | None]]) -> str:
    if not rows:
        return ""
    width = max((len(row) for row in rows), default=0)
    if width == 0:
        return ""
    values = [
        [clean_text(cell or "").replace("|", "\\|") for cell in row]
        + [""] * (width - len(row))
        for row in rows
    ]
    header = values[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in values[1:])
    return "\n".join(lines)


def _blocks_markdown(blocks: list[Any]) -> str:
    parts = [
        ("# " + block.text) if block.kind == "heading" else block.text
        for block in blocks
    ]
    return "\n\n".join(part for part in parts if part.strip()).strip()


def _extract_transcript_document(
    native: NormalizedDocument,
    pages: list[NativePdfPage],
    pages_requiring_ocr: list[int],
) -> NormalizedDocument:
    if not native.blocks:
        raise ExtractionFailure(
            "OCR_REQUIRED",
            "PDF transcript contains no native extractable text",
            partial_document=native,
        )
    metadata = _extract_transcript_metadata(
        "\n".join(page.native_markdown for page in pages)
    )
    warnings = list(native.quality.warnings)
    if metadata.video_name is None:
        warnings.append("transcript PDF has no source-provided video name")
    if metadata.time_range_start is None or metadata.time_range_end is None:
        warnings.append("transcript PDF has no source-provided overall time range")
    transcript = _with_transcript_metadata(native, metadata, warnings)
    if pages_requiring_ocr:
        transcript = _with_quality_warnings(
            transcript,
            [
                "transcript pages requiring OCR (not implemented): "
                + ", ".join(map(str, pages_requiring_ocr))
            ],
        )
        raise ExtractionFailure(
            "OCR_REQUIRED",
            "PDF transcript contains pages without native text; OCR is not implemented",
            partial_document=transcript,
        )
    return transcript


def _enrich_page(
    native: NativePdfPage,
    rendered: RenderedPage,
    content_profile: str,
    provider: VisionProvider,
    review_provider: SlideReviewProvider,
    prompt_version: str,
    review_prompt_version: str,
    render_version: str,
) -> NormalizedPage:
    started = time.perf_counter()
    result = provider.analyze_page(
        rendered.path,
        page_number=native.page_number,
        content_profile=content_profile,
        prompt_version=prompt_version,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    reconciliation = reconcile_page(
        native_markdown=native.native_markdown,
        native_elements=native.native_elements,
        vision=result,
    )
    native_payload = [
        element.model_dump(mode="json") for element in native.native_elements
    ]
    try:
        review_started = time.perf_counter()
        judge_result = review_provider.review_page(
            rendered.path,
            page_number=native.page_number,
            content_profile=content_profile,
            native_markdown=native.native_markdown,
            native_elements=native_payload,
            vision=result,
            reconciliation=reconciliation,
            prompt_version=review_prompt_version,
        )
        review_duration_ms = round((time.perf_counter() - review_started) * 1000)
        review = decide_page_review(
            vision=result,
            reconciliation=reconciliation,
            judge_result=judge_result,
            judge_provenance=SlideReviewProvenance(
                provider=review_provider.name,
                model=review_provider.model,
                prompt_version=review_prompt_version,
                response_hash=_model_hash(judge_result),
                duration_ms=review_duration_ms,
            ),
        )
    except (VisionProviderError, OSError, ValueError) as exc:
        review = failed_page_review(
            f"Independent PDF page review failed: {type(exc).__name__}: {exc}"
        )
    return NormalizedPage(
        page_number=native.page_number,
        native_block_ids=native.block_ids,
        native_markdown=native.native_markdown,
        native_elements=native.native_elements,
        selection_reasons=["all_pages_quality_pipeline"],
        native_text_character_count=native.text_character_count,
        max_image_pixels=native.max_image_pixels,
        vector_operation_count=native.vector_operation_count,
        image_path=rendered.relative_path,
        image_hash=rendered.content_hash,
        render_version=render_version,
        vision=result,
        vision_provenance=SlideVisionProvenance(
            provider=provider.name,
            model=provider.model,
            prompt_version=prompt_version,
            response_hash=_model_hash(result),
            duration_ms=duration_ms,
        ),
        reconciliation=reconciliation,
        review=review,
        combined_markdown=reconciliation.retrieval_markdown,
    )


def _model_hash(model: Any) -> str:
    payload = model.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _extract_transcript_metadata(text: str) -> TranscriptMetadata:
    video = TRANSCRIPT_VIDEO.search(text)
    time_range = TRANSCRIPT_TIME_RANGE.search(text)
    duration = None
    if time_range is not None and time_range.group("duration") is not None:
        duration = float(time_range.group("duration"))
    return TranscriptMetadata(
        video_name=video.group("name").strip() if video is not None else None,
        time_range_start=time_range.group("start") if time_range else None,
        time_range_end=time_range.group("end") if time_range else None,
        duration_seconds=duration,
    )


def _build_native_document(builder: DocumentBuilder) -> NormalizedDocument:
    if builder.blocks:
        return builder.build()
    return NormalizedDocument(
        source_filename=builder.source_filename,
        source_format=builder.source_format,
        content_hash=builder.content_hash,
        blocks=[],
        quality=ExtractionQuality(
            status="WARNING",
            block_count=0,
            text_character_count=0,
            warnings=["PDF has no native extractable text"],
        ),
    )


def _with_pages(
    native: NormalizedDocument,
    pages: list[NormalizedPage],
    warnings: list[str],
) -> NormalizedDocument:
    quality = native.quality.model_copy(
        update={
            "status": "WARNING" if warnings else "PASS",
            "warnings": list(dict.fromkeys(warnings)),
        }
    )
    return native.model_copy(
        update={"pages": link_pages(pages), "quality": quality}
    )


def _with_quality_warnings(
    native: NormalizedDocument, warnings: list[str]
) -> NormalizedDocument:
    quality = native.quality.model_copy(
        update={
            "status": "WARNING",
            "warnings": list(
                dict.fromkeys([*native.quality.warnings, *warnings])
            ),
        }
    )
    return native.model_copy(update={"quality": quality})


def _with_transcript_metadata(
    native: NormalizedDocument,
    metadata: TranscriptMetadata,
    warnings: list[str],
) -> NormalizedDocument:
    quality = native.quality.model_copy(
        update={
            "status": "WARNING" if warnings else "PASS",
            "warnings": list(dict.fromkeys(warnings)),
        }
    )
    return native.model_copy(
        update={"transcript_metadata": metadata, "quality": quality}
    )
