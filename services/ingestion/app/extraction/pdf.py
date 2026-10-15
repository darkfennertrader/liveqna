from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.generic import ContentStream, IndirectObject

from ..vision.base import VisionProvider, VisionProviderError
from ..vision.prompt import PDF_PROMPT_VERSION
from .common import DocumentBuilder
from .errors import ExtractionFailure
from .models import (
    ExtractionQuality,
    NormalizedDocument,
    NormalizedPage,
    SlideVisionProvenance,
    SlideVisionResult,
    SourceLocator,
    TranscriptMetadata,
)
from .pdf_renderer import PdfRenderer, RenderedPage


TRANSCRIPT_VIDEO = re.compile(
    r"^Video\s+\d+\s*:\s*(?P<name>.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
TRANSCRIPT_TIME_RANGE = re.compile(
    r"Time\s+Range\s*:\s*(?P<start>\d{1,2}:\d{2}:\d{2})\s+to\s+"
    r"(?P<end>\d{1,2}:\d{2}:\d{2})"
    r"(?:\s+Duration\s*:\s*(?P<duration>[0-9]+(?:\.[0-9]+)?)\s+seconds)?",
    re.IGNORECASE,
)
VECTOR_OPERATORS = {
    b"m",
    b"l",
    b"c",
    b"v",
    b"y",
    b"h",
    b"re",
    b"S",
    b"s",
    b"f",
    b"F",
    b"f*",
    b"B",
    b"B*",
    b"b",
    b"b*",
}
LARGE_IMAGE_PIXELS = 250_000
LOW_TEXT_CHARACTER_LIMIT = 250
COMPLEX_VECTOR_OPERATION_LIMIT = 1_000


@dataclass(frozen=True)
class NativePdfPage:
    page_number: int
    block_ids: list[str]
    native_markdown: str
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
    vision_prompt_version: str = PDF_PROMPT_VERSION,
    max_concurrency: int = 4,
) -> NormalizedDocument:
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ExtractionFailure(
                "ENCRYPTED_DOCUMENT",
                "encrypted PDFs are not supported in the first implementation",
            )
        builder = DocumentBuilder(source_filename, "pdf", content_hash)
        pages, pages_requiring_ocr, unclassified_pages = _extract_native_pages(
            reader, builder
        )
    except ExtractionFailure:
        raise
    except Exception as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "PDF file could not be parsed"
        ) from exc

    native_document = _build_native_document(builder)
    if document_type == "transcript":
        if not native_document.blocks:
            raise ExtractionFailure(
                "OCR_REQUIRED",
                "PDF transcript contains no extractable text",
                partial_document=native_document,
            )
        metadata = _extract_transcript_metadata(
            "\n".join(page.native_markdown for page in pages)
        )
        warnings = list(native_document.quality.warnings)
        if metadata.video_name is None:
            warnings.append("transcript PDF has no source-provided video name")
        if metadata.time_range_start is None or metadata.time_range_end is None:
            warnings.append(
                "transcript PDF has no source-provided overall time range"
            )
        transcript_document = _with_transcript_metadata(
            native_document, metadata, warnings
        )
        if pages_requiring_ocr:
            transcript_document = _with_quality_warnings(
                transcript_document,
                [
                    "transcript pages requiring OCR or visual resolution: "
                    + ", ".join(str(page) for page in pages_requiring_ocr)
                ],
            )
            raise ExtractionFailure(
                "OCR_REQUIRED",
                "PDF transcript contains pages without extractable text: "
                + ", ".join(str(page) for page in pages_requiring_ocr),
                partial_document=transcript_document,
            )
        return transcript_document

    if pages_requiring_ocr:
        document = _with_quality_warnings(
            native_document,
            [
                "pages requiring OCR or visual resolution: "
                + ", ".join(str(page) for page in pages_requiring_ocr)
            ],
        )
        raise ExtractionFailure(
            "OCR_REQUIRED",
            "PDF contains pages without extractable text: "
            + ", ".join(str(page) for page in pages_requiring_ocr),
            partial_document=document,
        )
    if unclassified_pages:
        document = _with_quality_warnings(
            native_document,
            [
                "page visual structure could not be classified: "
                + ", ".join(str(page) for page in unclassified_pages)
            ],
        )
        raise ExtractionFailure(
            "PDF_REVIEW_REQUIRED",
            "PDF page completeness could not be verified",
            partial_document=document,
        )

    visual_pages = [page for page in pages if _needs_visual_enrichment(page)]
    if not visual_pages:
        return native_document
    if renderer is None:
        raise ExtractionFailure(
            "PDF_RENDERER_REQUIRED",
            "PDF visual extraction requires a configured page renderer",
            partial_document=native_document,
        )
    if vision_provider is None:
        raise ExtractionFailure(
            "PDF_VISION_PROVIDER_REQUIRED",
            "PDF visual extraction requires a configured vision provider",
            partial_document=native_document,
        )

    try:
        rendered = renderer.render(
            path,
            len(reader.pages),
            [page.page_number for page in visual_pages],
        )
    except ExtractionFailure as exc:
        raise ExtractionFailure(
            exc.code,
            str(exc),
            partial_document=native_document,
        ) from exc
    rendered_by_number = {item.page_number: item for item in rendered}
    missing = [
        page.page_number
        for page in visual_pages
        if page.page_number not in rendered_by_number
    ]
    if missing:
        raise ExtractionFailure(
            "PDF_RENDER_FAILED",
            "renderer omitted selected pages: "
            + ", ".join(str(page) for page in missing),
            partial_document=native_document,
        )

    completed: dict[int, NormalizedPage] = {}
    failures: list[str] = []
    workers = max(1, min(max_concurrency, len(visual_pages)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _enrich_page,
                native,
                rendered_by_number[native.page_number],
                content_profile,
                vision_provider,
                vision_prompt_version,
                renderer.version,
            ): native.page_number
            for native in visual_pages
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
    document = _with_pages(native_document, normalized_pages, warnings)
    if failures:
        raise ExtractionFailure(
            "PDF_VISION_FAILED",
            "visual extraction failed for " + "; ".join(sorted(failures)),
            partial_document=document,
        )

    material = [
        f"page {page.page_number}: {item.description}"
        for page in normalized_pages
        for item in page.vision.discrepancies
        if item.material
    ]
    if material:
        raise ExtractionFailure(
            "PDF_REVIEW_REQUIRED",
            "material native/visual discrepancies require review: "
            + "; ".join(material),
            partial_document=document,
        )
    return document


def _extract_native_pages(
    reader: PdfReader, builder: DocumentBuilder
) -> tuple[list[NativePdfPage], list[int], list[int]]:
    pages: list[NativePdfPage] = []
    pages_requiring_ocr: list[int] = []
    unclassified_pages: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        before = len(builder.blocks)
        text = page.extract_text() or ""
        paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
        if not paragraphs and text.strip():
            paragraphs = [text.strip()]
        for paragraph in paragraphs:
            builder.add("paragraph", paragraph, SourceLocator(page=page_number))

        try:
            max_image_pixels = _max_image_pixels(page)
            vector_operation_count = _vector_operation_count(page, reader)
        except Exception:
            max_image_pixels = 0
            vector_operation_count = 0
            unclassified_pages.append(page_number)

        native_blocks = list(builder.blocks[before:])
        cleaned_text = text.strip()
        if not cleaned_text:
            pages_requiring_ocr.append(page_number)
        pages.append(
            NativePdfPage(
                page_number=page_number,
                block_ids=[block.id for block in native_blocks],
                native_markdown="\n\n".join(
                    block.text for block in native_blocks
                ),
                text_character_count=len(cleaned_text),
                max_image_pixels=max_image_pixels,
                vector_operation_count=vector_operation_count,
            )
        )
    return pages, pages_requiring_ocr, unclassified_pages


def _needs_visual_enrichment(page: NativePdfPage) -> bool:
    return bool(_visual_selection_reasons(page))


def _visual_selection_reasons(page: NativePdfPage) -> list[str]:
    reasons: list[str] = []
    image_dominant = (
        page.text_character_count < LOW_TEXT_CHARACTER_LIMIT
        and page.max_image_pixels >= LARGE_IMAGE_PIXELS
    )
    vector_complex = (
        page.vector_operation_count >= COMPLEX_VECTOR_OPERATION_LIMIT
    )
    if image_dominant:
        reasons.append("image_dominant_low_text")
    if vector_complex:
        reasons.append("complex_vector_graphics")
    return reasons


def _vector_operation_count(page: Any, reader: PdfReader) -> int:
    contents = page.get_contents()
    if contents is None:
        return 0
    stream = ContentStream(contents, reader)
    return sum(
        1 for _operands, operator in stream.operations if operator in VECTOR_OPERATORS
    )


def _max_image_pixels(page: Any) -> int:
    return max(
        (
            width * height
            for width, height in _image_dimensions(page, set())
        ),
        default=0,
    )


def _image_dimensions(
    container: Any, seen: set[tuple[int, int] | int]
) -> Iterable[tuple[int, int]]:
    resolved = _resolve(container)
    resources = _resolve(resolved.get("/Resources", {}))
    xobjects = _resolve(resources.get("/XObject", {}))
    if not hasattr(xobjects, "items"):
        return
    for _name, reference in xobjects.items():
        key: tuple[int, int] | int
        if isinstance(reference, IndirectObject):
            key = (reference.idnum, reference.generation)
        else:
            key = id(reference)
        if key in seen:
            continue
        seen.add(key)
        item = _resolve(reference)
        subtype = item.get("/Subtype")
        if subtype == "/Image":
            yield int(item.get("/Width", 0)), int(item.get("/Height", 0))
        elif subtype == "/Form":
            yield from _image_dimensions(item, seen)


def _resolve(value: Any) -> Any:
    return value.get_object() if isinstance(value, IndirectObject) else value


def _extract_transcript_metadata(text: str) -> TranscriptMetadata:
    video = TRANSCRIPT_VIDEO.search(text)
    time_range = TRANSCRIPT_TIME_RANGE.search(text)
    duration = None
    if time_range is not None and time_range.group("duration") is not None:
        duration = float(time_range.group("duration"))
    return TranscriptMetadata(
        video_name=video.group("name").strip() if video is not None else None,
        time_range_start=(
            time_range.group("start") if time_range is not None else None
        ),
        time_range_end=(
            time_range.group("end") if time_range is not None else None
        ),
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


def _enrich_page(
    native: NativePdfPage,
    rendered: RenderedPage,
    content_profile: str,
    provider: VisionProvider,
    prompt_version: str,
    render_version: str,
) -> NormalizedPage:
    started = time.perf_counter()
    result = provider.analyze_page(
        rendered.path,
        page_number=native.page_number,
        content_profile=content_profile,
        native_markdown=native.native_markdown,
        prompt_version=prompt_version,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    response_payload = result.model_dump(mode="json")
    response_hash = hashlib.sha256(
        json.dumps(
            response_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return NormalizedPage(
        page_number=native.page_number,
        native_block_ids=native.block_ids,
        native_markdown=native.native_markdown,
        selection_reasons=_visual_selection_reasons(native),
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
            response_hash=response_hash,
            duration_ms=duration_ms,
        ),
        combined_markdown=_combined_markdown(native, result),
    )


def _combined_markdown(
    native: NativePdfPage, vision: SlideVisionResult
) -> str:
    sections: list[str] = []
    if native.native_markdown:
        sections.append(native.native_markdown)
    if vision.transcription_markdown.strip():
        sections.append(
            "## Visual transcription\n\n"
            + vision.transcription_markdown.strip()
        )
    if vision.tables_markdown:
        tables = "\n\n".join(
            item.strip() for item in vision.tables_markdown if item.strip()
        )
        if tables:
            sections.append("## Visual tables\n\n" + tables)
    if vision.visual_description.strip():
        sections.append(
            "## Visual evidence\n\n" + vision.visual_description.strip()
        )
    labels = list(
        dict.fromkeys(label.strip() for label in vision.labels if label.strip())
    )
    if labels:
        sections.append("## Retrieval labels\n\n" + ", ".join(labels))
    return "\n\n".join(section for section in sections if section).strip()


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
    return native.model_copy(update={"pages": pages, "quality": quality})


def _with_quality_warnings(
    native: NormalizedDocument, warnings: list[str]
) -> NormalizedDocument:
    return _with_pages(
        native,
        list(native.pages),
        list(native.quality.warnings) + warnings,
    )


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
