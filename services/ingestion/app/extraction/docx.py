from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import ZipFile

import pymupdf
from defusedxml.ElementTree import fromstring

from ..vision.base import SlideReviewProvider, VisionProvider, VisionProviderError
from ..vision.prompt import DOCX_PROMPT_VERSION, DOCX_REVIEW_PROMPT_VERSION
from .common import DocumentBuilder, clean_text
from .docx_renderer import DocxRenderer, RenderedPage
from .docx_validation import (
    DocxPackageInventory,
    DocxPackageLimits,
    validate_docx_package,
)
from .errors import ExtractionFailure
from .models import (
    DocumentNativeElement,
    DocxMetadata,
    NormalizedBlock,
    NormalizedDocument,
    NormalizedPage,
    PageNativeElement,
    SlideReviewDecisionFinding,
    SlideReviewProvenance,
    SlideVisionProvenance,
    SourceLocator,
)
from .pdf_link import link_pages
from .pptx_reconcile import reconcile_word_page
from .pptx_review import decide_word_page_review, failed_word_page_review
from .text import transcript_parts

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


@dataclass
class _NativeRecord:
    block_id: str
    element: DocumentNativeElement
    story: str
    reading_order: int


@dataclass
class _NativeResult:
    document: NormalizedDocument
    records: list[_NativeRecord]
    extra_elements: list[DocumentNativeElement]
    deterministic_findings: list[SlideReviewDecisionFinding]


@dataclass
class _ExtractionState:
    builder: DocumentBuilder
    transcript: bool
    revision_policy: Literal["final", "original"]
    styles: dict[str, tuple[str, int | None]]
    records: list[_NativeRecord] = field(default_factory=list)
    extra_elements: list[DocumentNativeElement] = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)
    reading_order: int = 0


@dataclass(frozen=True)
class _PageEvidence:
    page_number: int
    block_ids: list[str]
    native_markdown: str
    native_elements: list[PageNativeElement]
    text_character_count: int
    max_image_pixels: int
    vector_operation_count: int
    deterministic_findings: list[SlideReviewDecisionFinding]


def extract_docx(
    path: Path,
    source_filename: str,
    content_hash: str,
    *,
    transcript: bool,
    content_profile: str = "general_document",
    renderer: DocxRenderer | None = None,
    vision_provider: VisionProvider | None = None,
    review_provider: SlideReviewProvider | None = None,
    vision_prompt_version: str = DOCX_PROMPT_VERSION,
    review_prompt_version: str = DOCX_REVIEW_PROMPT_VERSION,
    max_concurrency: int = 4,
    package_limits: DocxPackageLimits | None = None,
    revision_policy: Literal["final", "original"] = "final",
) -> NormalizedDocument:
    limits = package_limits or DocxPackageLimits()
    inventory = validate_docx_package(path, limits)
    native = _extract_native(
        path,
        source_filename,
        content_hash,
        transcript=transcript,
        revision_policy=revision_policy,
        inventory=inventory,
    )
    if renderer is None:
        raise ExtractionFailure(
            "DOCX_RENDERER_REQUIRED",
            "DOCX evidence extraction requires a configured Writer renderer",
            partial_document=native.document,
        )
    if vision_provider is None:
        raise ExtractionFailure(
            "DOCX_VISION_PROVIDER_REQUIRED",
            "DOCX evidence extraction requires a configured vision provider",
            partial_document=native.document,
        )
    if review_provider is None:
        raise ExtractionFailure(
            "DOCX_REVIEW_PROVIDER_REQUIRED",
            "DOCX evidence extraction requires an independent review provider",
            partial_document=native.document,
        )

    try:
        rendered = renderer.render(path, max_pages=limits.max_pages)
        page_evidence, updated_blocks, mapping_warnings = _map_native_to_pages(
            native, rendered.pdf_path, rendered.pages
        )
        native_document = native.document.model_copy(update={"blocks": updated_blocks})
    except ExtractionFailure as exc:
        raise ExtractionFailure(
            exc.code, str(exc), partial_document=native.document
        ) from exc

    completed: dict[int, NormalizedPage] = {}
    failures: list[str] = []
    workers = max(1, min(max_concurrency, len(page_evidence)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _enrich_page,
                evidence,
                rendered.pages[evidence.page_number - 1],
                content_profile,
                vision_provider,
                review_provider,
                vision_prompt_version,
                review_prompt_version,
                renderer.version,
            ): evidence.page_number
            for evidence in page_evidence
        }
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                completed[page_number] = future.result()
            except (VisionProviderError, OSError, ValueError) as exc:
                failures.append(
                    f"page {page_number}: {type(exc).__name__}: {exc}"
                )

    pages = [completed[number] for number in sorted(completed)]
    warnings = list(native_document.quality.warnings)
    warnings.extend(mapping_warnings)
    for page in pages:
        warnings.extend(
            f"page {page.page_number} uncertainty: {item}"
            for item in page.vision.uncertainties
        )
    normalized = _with_pages(native_document, pages, warnings)
    if failures:
        raise ExtractionFailure(
            "DOCX_VISION_FAILED",
            "DOCX page processing failed for " + "; ".join(sorted(failures)),
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
            "DOCX_REVIEW_REQUIRED",
            "material native/visual discrepancies require review: "
            + "; ".join(material),
            partial_document=normalized,
        )
    return normalized


def _extract_native(
    path: Path,
    source_filename: str,
    content_hash: str,
    *,
    transcript: bool,
    revision_policy: Literal["final", "original"],
    inventory: DocxPackageInventory,
) -> _NativeResult:
    builder = DocumentBuilder(source_filename, "docx", content_hash)
    deterministic_findings: list[SlideReviewDecisionFinding] = []
    try:
        with ZipFile(path) as archive:
            state = _ExtractionState(
                builder=builder,
                transcript=transcript,
                revision_policy=revision_policy,
                styles=_style_inventory(archive),
            )
            story_roots = []
            for part_name in inventory.story_parts:
                root = fromstring(archive.read(part_name))
                story_roots.append(root)
                story = _story_name(part_name)
                state.builder.section_path = []
                _process_story(
                    root,
                    part_name,
                    story,
                    _relationships(archive, part_name),
                    state,
                )

            document_root = fromstring(archive.read("word/document.xml"))
            inserted = sum(1 for root in story_roots for _ in root.iter(W + "ins"))
            deleted = sum(1 for root in story_roots for _ in root.iter(W + "del"))
            content_controls = sum(
                1 for root in story_roots for _ in root.iter(W + "sdt")
            )
            text_boxes = sum(
                1 for root in story_roots for _ in root.iter(W + "txbxContent")
            )
            metadata = DocxMetadata(
                revision_policy=revision_policy,
                story_parts=list(inventory.story_parts),
                section_count=max(1, sum(1 for _ in document_root.iter(W + "sectPr"))),
                paragraph_count=state.counters["paragraph"],
                table_count=state.counters["table"],
                image_count=state.counters["image"],
                content_control_count=content_controls,
                text_box_count=text_boxes,
                comment_count=state.counters["comment"],
                footnote_count=state.counters["footnote"],
                endnote_count=state.counters["endnote"],
                inserted_revision_count=inserted,
                deleted_revision_count=deleted,
                external_links=list(inventory.external_links),
                embedded_objects=list(inventory.embedded_objects),
                core_properties=_properties(archive, "docProps/core.xml"),
                custom_properties=_properties(archive, "docProps/custom.xml"),
                package_entry_count=inventory.entry_count,
                package_compressed_bytes=inventory.compressed_bytes,
                package_uncompressed_bytes=inventory.uncompressed_bytes,
                package_compression_ratio=inventory.compression_ratio,
            )
    except ExtractionFailure:
        raise
    except Exception as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "DOCX native OOXML extraction failed"
        ) from exc

    if inserted or deleted:
        deterministic_findings.append(
            _finding(
                "PARTIAL_OR_UNCERTAIN",
                "MEDIUM",
                "The DOCX contains tracked revisions and was normalized using "
                f"the configured '{revision_policy}' revision view.",
                f"Inserted revision containers: {inserted}; deleted containers: {deleted}.",
            )
        )
        builder.warnings.append(
            f"tracked revisions present; applied {revision_policy} revision policy"
        )
    if metadata.comment_count:
        deterministic_findings.append(
            _finding(
                "PARTIAL_OR_UNCERTAIN",
                "MEDIUM",
                "The DOCX contains review comments; comment text is retained for "
                "audit but excluded from retrieval by default.",
                f"Comment count: {metadata.comment_count}.",
            )
        )
        builder.warnings.append("review comments are excluded from retrieval")
    if inventory.embedded_objects:
        deterministic_findings.append(
            _finding(
                "MISSING_KEY_ELEMENT",
                "HIGH",
                "The DOCX contains embedded or active content that cannot be "
                "safely normalized as native Word text.",
                ", ".join(inventory.embedded_objects),
            )
        )
        builder.warnings.append("embedded objects require human review")

    if not builder.blocks:
        raise ExtractionFailure("EMPTY_DOCUMENT", "no usable DOCX content was extracted")
    document = builder.build().model_copy(update={"docx_metadata": metadata})
    return _NativeResult(
        document=document,
        records=state.records,
        extra_elements=state.extra_elements,
        deterministic_findings=deterministic_findings,
    )


def _process_story(
    root,
    part_name: str,
    story: str,
    relationships: dict[str, dict[str, str]],
    state: _ExtractionState,
) -> None:
    if story in {"footnote", "endnote", "comment"}:
        for container in root.findall(W + story):
            identifier = container.attrib.get(W + "id", "")
            if story in {"footnote", "endnote"} and identifier in {"-1", "0"}:
                continue
            state.counters[story] += 1
            _walk_blocks(
                container,
                part_name,
                story,
                relationships,
                state,
                retrieval_eligible=story != "comment",
            )
        return
    body = root.find(W + "body") if story == "main" else root
    if body is not None:
        _walk_blocks(body, part_name, story, relationships, state)


def _walk_blocks(
    container,
    part_name: str,
    story: str,
    relationships: dict[str, dict[str, str]],
    state: _ExtractionState,
    *,
    retrieval_eligible: bool = True,
    parent_table: int | None = None,
) -> None:
    for child in _unwrapped_children(container):
        tag = _local(child.tag)
        if tag == "p":
            _add_paragraph(
                child,
                part_name,
                story,
                relationships,
                state,
                retrieval_eligible=retrieval_eligible,
            )
        elif tag == "tbl":
            _add_table(
                child,
                part_name,
                story,
                relationships,
                state,
                retrieval_eligible=retrieval_eligible,
                parent_table=parent_table,
            )
        elif tag not in {"sectPr", "bookmarkStart", "bookmarkEnd"}:
            _walk_blocks(
                child,
                part_name,
                story,
                relationships,
                state,
                retrieval_eligible=retrieval_eligible,
                parent_table=parent_table,
            )


def _add_paragraph(
    paragraph,
    part_name: str,
    story: str,
    relationships: dict[str, dict[str, str]],
    state: _ExtractionState,
    *,
    retrieval_eligible: bool,
) -> None:
    state.counters["paragraph"] += 1
    paragraph_number = state.counters["paragraph"]
    text = clean_text(_visible_text(paragraph, state.revision_policy))
    links = _paragraph_links(paragraph, relationships, state.revision_policy)
    style_id, style_name, heading_level = _paragraph_style(paragraph, state.styles)
    source = SourceLocator(paragraph=paragraph_number, story=story, part=part_name)
    attributes = {
        "style_id": style_id,
        "style_name": style_name,
        "hyperlinks": links,
        "revision_policy": state.revision_policy,
    }
    before = len(state.builder.blocks)
    if text:
        if state.transcript and story == "main" and heading_level is None:
            timestamp, speaker, body = transcript_parts(text)
            source = source.model_copy(update={"timestamp": timestamp, "speaker": speaker})
            state.builder.add(
                "transcript_turn" if speaker else "paragraph",
                body,
                source,
                retrieval_eligible=retrieval_eligible,
                attributes=attributes,
            )
        elif heading_level is not None:
            state.builder.heading(
                text,
                source,
                level=heading_level,
                retrieval_eligible=retrieval_eligible,
                attributes=attributes,
            )
        else:
            state.builder.add(
                "paragraph",
                text,
                source,
                retrieval_eligible=retrieval_eligible,
                attributes=attributes,
            )
    new_blocks = state.builder.blocks[before:]
    anchor_ids = [block.id for block in new_blocks]
    if new_blocks:
        state.reading_order += 1
        block = new_blocks[0]
        state.records.append(
            _NativeRecord(
                block_id=block.id,
                story=story,
                reading_order=state.reading_order,
                element=DocumentNativeElement(
                    element_id=f"docx-element-{state.reading_order:06d}",
                    kind="title" if block.kind == "heading" else "text",
                    content=("# " + block.text) if block.kind == "heading" else block.text,
                    block_ids=[block.id],
                    reading_order=state.reading_order,
                    attributes={**attributes, "story": story, "part": part_name},
                ),
            )
        )
    _paragraph_images(
        paragraph, part_name, story, relationships, state, anchor_ids
    )


def _add_table(
    table,
    part_name: str,
    story: str,
    relationships: dict[str, dict[str, str]],
    state: _ExtractionState,
    *,
    retrieval_eligible: bool,
    parent_table: int | None,
) -> None:
    state.counters["table"] += 1
    table_number = state.counters["table"]
    rows: list[list[str]] = []
    cell_audit: list[dict[str, Any]] = []
    cells_for_nested = []
    for row_number, row in enumerate(_table_rows(table), start=1):
        values = []
        column_number = 1
        for cell in _row_cells(row):
            text = clean_text(_cell_text(cell, state.revision_policy))
            grid_span = _integer_property(cell, "gridSpan", default=1)
            values.append(text)
            cell_audit.append(
                {
                    "row": row_number,
                    "column": column_number,
                    "text": text,
                    "grid_span": grid_span,
                    "vertical_merge": _property(cell, "vMerge"),
                }
            )
            column_number += grid_span
            cells_for_nested.append(cell)
        if values:
            rows.append(values)
    markdown = _table_markdown(rows)
    before = len(state.builder.blocks)
    if markdown:
        state.builder.add(
            "table",
            markdown,
            SourceLocator(table=table_number, story=story, part=part_name),
            retrieval_eligible=retrieval_eligible,
            attributes={
                "story": story,
                "part": part_name,
                "parent_table": parent_table,
                "rows": len(rows),
                "columns": max((len(row) for row in rows), default=0),
                "cells": cell_audit,
            },
        )
    new_blocks = state.builder.blocks[before:]
    if new_blocks:
        state.reading_order += 1
        block = new_blocks[0]
        state.records.append(
            _NativeRecord(
                block_id=block.id,
                story=story,
                reading_order=state.reading_order,
                element=DocumentNativeElement(
                    element_id=f"docx-element-{state.reading_order:06d}",
                    kind="table",
                    content=block.text,
                    block_ids=[block.id],
                    reading_order=state.reading_order,
                    attributes=block.attributes,
                ),
            )
        )
    for cell in cells_for_nested:
        for child in _unwrapped_children(cell):
            if _local(child.tag) == "tbl":
                _add_table(
                    child,
                    part_name,
                    story,
                    relationships,
                    state,
                    retrieval_eligible=retrieval_eligible,
                    parent_table=table_number,
                )


def _map_native_to_pages(
    native: _NativeResult,
    pdf_path: Path,
    rendered_pages: list[RenderedPage],
) -> tuple[list[_PageEvidence], list[NormalizedBlock], list[str]]:
    document = pymupdf.open(pdf_path)
    if document.page_count != len(rendered_pages):
        document.close()
        raise ExtractionFailure(
            "DOCX_RENDER_FAILED",
            "rendered DOCX page count changed during native alignment",
        )
    page_texts = [clean_text(page.get_text("text", sort=True)) for page in document]
    normalized_page_texts = [_match_text(text) for text in page_texts]
    assignments: dict[str, list[int]] = {}
    unmapped: list[str] = []
    last_main_page = 1
    block_by_id = {block.id: block for block in native.document.blocks}
    for record in native.records:
        block = block_by_id[record.block_id]
        scores = [
            _page_match_score(block.text, page_text)
            for page_text in normalized_page_texts
        ]
        normalized_block = _match_text(block.text)
        exact_pages = [
            index + 1
            for index, page_text in enumerate(normalized_page_texts)
            if normalized_block and normalized_block in page_text
        ]
        if record.story in {"header", "footer"} and exact_pages:
            selected = exact_pages
        elif block.kind == "table":
            selected = [
                index + 1
                for index, score in enumerate(scores)
                if score >= 0.42
            ]
            if not selected and scores:
                best_score = max(scores)
                selected = [scores.index(best_score) + 1]
                if best_score < 0.18:
                    unmapped.append(record.block_id)
        elif scores:
            start = last_main_page - 1 if record.story == "main" else 0
            eligible = list(enumerate(scores[start:], start=start))
            best_index, best_score = max(eligible, key=lambda item: item[1])
            selected = [best_index + 1]
            if best_score < 0.18:
                unmapped.append(record.block_id)
        else:
            selected = [1]
            unmapped.append(record.block_id)
        assignments[record.block_id] = selected
        if record.story == "main":
            last_main_page = max(last_main_page, selected[0])

    updated_blocks = []
    for block in native.document.blocks:
        pages = assignments.get(block.id, [1])
        updated_blocks.append(
            block.model_copy(
                update={
                    "source": block.source.model_copy(update={"page": pages[0]}),
                    "attributes": {
                        **block.attributes,
                        "rendered_pages": pages,
                    },
                }
            )
        )
    updated_by_id = {block.id: block for block in updated_blocks}
    evidence: list[_PageEvidence] = []
    warnings: list[str] = []
    if unmapped:
        warnings.append(
            f"{len(unmapped)} native DOCX blocks could not be confidently "
            "located on rendered pages"
        )
    for page_number, page in enumerate(document, start=1):
        block_ids = [
            record.block_id
            for record in native.records
            if page_number in assignments.get(record.block_id, [])
        ]
        blocks = [updated_by_id[block_id] for block_id in block_ids]
        markdown = _blocks_markdown(blocks)
        page_elements = [
            PageNativeElement.model_validate(record.element.model_dump(mode="json"))
            for record in native.records
            if page_number in assignments.get(record.block_id, [])
        ]
        for element in native.extra_elements:
            anchor_pages = {
                assigned
                for block_id in element.block_ids
                for assigned in assignments.get(block_id, [])
            }
            if page_number in (anchor_pages or {1}):
                page_elements.append(
                    PageNativeElement.model_validate(element.model_dump(mode="json"))
                )
        raw = page.get_text("dict", sort=True)
        image_pixels = max(
            (
                int(item.get("width", 0)) * int(item.get("height", 0))
                for item in raw.get("blocks", [])
                if item.get("type") == 1
            ),
            default=0,
        )
        findings = list(native.deterministic_findings if page_number == 1 else [])
        page_unmapped = [block_id for block_id in block_ids if block_id in unmapped]
        if page_unmapped:
            findings.append(
                _finding(
                    "MISSING_KEY_ELEMENT",
                    "HIGH",
                    "Native DOCX content could not be confidently aligned to "
                    "the rendered page.",
                    "Unmapped normalized blocks: " + ", ".join(page_unmapped),
                    native_element_ids=[
                        element.element_id
                        for element in page_elements
                        if any(block_id in page_unmapped for block_id in element.block_ids)
                    ],
                )
            )
        evidence.append(
            _PageEvidence(
                page_number=page_number,
                block_ids=block_ids,
                native_markdown=markdown,
                native_elements=page_elements,
                text_character_count=sum(len(block.text) for block in blocks),
                max_image_pixels=image_pixels,
                vector_operation_count=len(page.get_drawings()),
                deterministic_findings=findings,
            )
        )
    document.close()
    return evidence, updated_blocks, warnings


def _enrich_page(
    native: _PageEvidence,
    rendered: RenderedPage,
    content_profile: str,
    provider: VisionProvider,
    review_provider: SlideReviewProvider,
    prompt_version: str,
    review_prompt_version: str,
    render_version: str,
) -> NormalizedPage:
    started = time.perf_counter()
    result = provider.analyze_word_page(
        rendered.path,
        page_number=native.page_number,
        content_profile=content_profile,
        prompt_version=prompt_version,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    reconciliation = reconcile_word_page(
        native_markdown=native.native_markdown,
        native_elements=native.native_elements,
        vision=result,
    )
    native_payload = [
        element.model_dump(mode="json") for element in native.native_elements
    ]
    try:
        review_started = time.perf_counter()
        judge_result = review_provider.review_word_page(
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
        review = decide_word_page_review(
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
            deterministic_findings=native.deterministic_findings,
        )
    except (VisionProviderError, OSError, ValueError) as exc:
        review = failed_word_page_review(
            f"Independent DOCX page review failed: {type(exc).__name__}: {exc}"
        )
    return NormalizedPage(
        page_number=native.page_number,
        native_block_ids=native.block_ids,
        native_markdown=native.native_markdown,
        native_elements=native.native_elements,
        selection_reasons=[
            "all_pages_quality_pipeline",
            "docx_native_to_rendered_alignment",
        ],
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
    return native.model_copy(update={"pages": link_pages(pages), "quality": quality})


def _visible_text(element, policy: Literal["final", "original"]) -> str:
    tag = _local(element.tag)
    if policy == "final" and tag in {"del", "moveFrom"}:
        return ""
    if policy == "original" and tag in {"ins", "moveTo"}:
        return ""
    if tag in {"t", "delText"}:
        return element.text or ""
    if tag == "tab":
        return "\t"
    if tag in {"br", "cr"}:
        return "\n"
    if tag == "noBreakHyphen":
        return "-"
    if tag in {"instrText", "fldChar"}:
        return ""
    return "".join(_visible_text(child, policy) for child in element)


def _unwrapped_children(container):
    for child in container:
        if _local(child.tag) == "sdt":
            content = child.find(W + "sdtContent")
            if content is not None:
                yield from _unwrapped_children(content)
        else:
            yield child


def _table_rows(table):
    for child in _unwrapped_children(table):
        if _local(child.tag) == "tr":
            yield child


def _row_cells(row):
    for child in _unwrapped_children(row):
        if _local(child.tag) == "tc":
            yield child


def _cell_text(cell, policy: Literal["final", "original"]) -> str:
    values = []
    for child in _unwrapped_children(cell):
        if _local(child.tag) == "p":
            text = clean_text(_visible_text(child, policy))
            if text:
                values.append(text)
    return "\n".join(values)


def _table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    values = [
        [cell.replace("|", "\\|") for cell in row] + [""] * (width - len(row))
        for row in rows
    ]
    header = values[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in values[1:])
    return "\n".join(lines)


def _paragraph_style(
    paragraph, styles: dict[str, tuple[str, int | None]]
) -> tuple[str | None, str | None, int | None]:
    properties = paragraph.find(W + "pPr")
    style_id = None
    direct_outline = None
    if properties is not None:
        style = properties.find(W + "pStyle")
        if style is not None:
            style_id = style.attrib.get(W + "val")
        outline = properties.find(W + "outlineLvl")
        if outline is not None:
            raw = outline.attrib.get(W + "val", "")
            if raw.isdigit():
                direct_outline = int(raw) + 1
    style_name, style_outline = styles.get(style_id or "", (style_id or "", None))
    level = direct_outline or style_outline
    if level is None and style_name:
        match = re.match(r"heading\s*(\d+)", style_name, re.IGNORECASE)
        if match:
            level = int(match.group(1))
    return style_id, style_name or None, min(level, 6) if level else None


def _style_inventory(archive: ZipFile) -> dict[str, tuple[str, int | None]]:
    if "word/styles.xml" not in archive.namelist():
        return {}
    root = fromstring(archive.read("word/styles.xml"))
    result = {}
    for style in root.findall(W + "style"):
        if style.attrib.get(W + "type") != "paragraph":
            continue
        style_id = style.attrib.get(W + "styleId", "")
        name = style.find(W + "name")
        name_value = name.attrib.get(W + "val", style_id) if name is not None else style_id
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        level = None
        if outline is not None and outline.attrib.get(W + "val", "").isdigit():
            level = int(outline.attrib[W + "val"]) + 1
        result[style_id] = (name_value, level)
    return result


def _paragraph_links(
    paragraph,
    relationships: dict[str, dict[str, str]],
    policy: Literal["final", "original"],
) -> list[dict]:
    links = []
    for hyperlink in paragraph.iter(W + "hyperlink"):
        relationship_id = hyperlink.attrib.get(R + "id")
        relationship = relationships.get(relationship_id or "", {})
        target = relationship.get("target") or hyperlink.attrib.get(W + "anchor")
        if target:
            links.append(
                {
                    "text": clean_text(_visible_text(hyperlink, policy)),
                    "target": target,
                    "external": relationship.get("external") == "true",
                }
            )
    return links


def _paragraph_images(
    paragraph,
    part_name: str,
    story: str,
    relationships: dict[str, dict[str, str]],
    state: _ExtractionState,
    anchor_ids: list[str],
) -> None:
    relationship_ids = []
    for element in paragraph.iter():
        if element.tag == A + "blip":
            relationship_id = element.attrib.get(R + "embed") or element.attrib.get(R + "link")
            if relationship_id:
                relationship_ids.append(relationship_id)
        elif _local(element.tag) == "imagedata":
            relationship_id = element.attrib.get(R + "id")
            if relationship_id:
                relationship_ids.append(relationship_id)
    descriptions = []
    for element in paragraph.iter(WP + "docPr"):
        for attribute in ("descr", "title", "name"):
            value = element.attrib.get(attribute, "").strip()
            if value and value not in descriptions:
                descriptions.append(value)
    for relationship_id in dict.fromkeys(relationship_ids):
        state.counters["image"] += 1
        state.reading_order += 1
        relationship = relationships.get(relationship_id, {})
        state.extra_elements.append(
            DocumentNativeElement(
                element_id=f"docx-image-{state.counters['image']:06d}",
                kind="image",
                content="; ".join(descriptions),
                block_ids=anchor_ids,
                reading_order=state.reading_order,
                attributes={
                    "story": story,
                    "part": part_name,
                    "relationship_id": relationship_id,
                    "target": relationship.get("target"),
                    "external": relationship.get("external") == "true",
                    "alternative_text": descriptions,
                },
            )
        )


def _relationships(archive: ZipFile, part_name: str) -> dict[str, dict[str, str]]:
    part = PurePosixPath(part_name)
    relationships_name = str(part.parent / "_rels" / f"{part.name}.rels")
    if relationships_name not in archive.namelist():
        return {}
    root = fromstring(archive.read(relationships_name))
    result = {}
    for relationship in root.iter(PKG_REL + "Relationship"):
        identifier = relationship.attrib.get("Id")
        if identifier:
            result[identifier] = {
                "target": relationship.attrib.get("Target", ""),
                "type": relationship.attrib.get("Type", ""),
                "external": str(relationship.attrib.get("TargetMode") == "External").lower(),
            }
    return result


def _story_name(part_name: str) -> str:
    name = PurePosixPath(part_name).name
    if name == "document.xml":
        return "main"
    if name.startswith("header"):
        return "header"
    if name.startswith("footer"):
        return "footer"
    if name == "footnotes.xml":
        return "footnote"
    if name == "endnotes.xml":
        return "endnote"
    if name == "comments.xml":
        return "comment"
    return "other"


def _properties(archive: ZipFile, name: str) -> dict[str, Any]:
    if name not in archive.namelist():
        return {}
    root = fromstring(archive.read(name))
    result: dict[str, Any] = {}
    for child in root:
        key = child.attrib.get("name") or _local(child.tag)
        value = clean_text("".join(child.itertext()))
        if value:
            result[key] = value
    return result


def _property(cell, name: str) -> str | None:
    properties = cell.find(W + "tcPr")
    element = properties.find(W + name) if properties is not None else None
    return element.attrib.get(W + "val", "continue") if element is not None else None


def _integer_property(cell, name: str, *, default: int) -> int:
    value = _property(cell, name)
    return int(value) if value and value.isdigit() else default


def _match_text(value: str) -> str:
    return _SPACE.sub(" ", " ".join(_TOKEN.findall(value.casefold()))).strip()


def _page_match_score(block_text: str, normalized_page_text: str) -> float:
    block = _match_text(block_text)
    if not block or not normalized_page_text:
        return 0.0
    if block in normalized_page_text:
        return 1.0
    block_tokens = _TOKEN.findall(block)
    page_tokens = set(_TOKEN.findall(normalized_page_text))
    if not block_tokens:
        return 0.0
    overlap = sum(1 for token in block_tokens if token in page_tokens)
    recall = overlap / len(block_tokens)
    precision = overlap / max(len(page_tokens), 1)
    sequence = SequenceMatcher(
        None, block[:4000], normalized_page_text[:12000], autojunk=False
    ).ratio()
    return round(0.7 * recall + 0.1 * precision + 0.2 * sequence, 4)


def _blocks_markdown(blocks: list[NormalizedBlock]) -> str:
    return "\n\n".join(
        ("# " + block.text) if block.kind == "heading" else block.text
        for block in blocks
        if block.text.strip() and block.retrieval_eligible
    ).strip()


def _finding(
    category: str,
    severity: str,
    description: str,
    evidence: str,
    *,
    native_element_ids: list[str] | None = None,
) -> SlideReviewDecisionFinding:
    return SlideReviewDecisionFinding(
        category=category,
        severity=severity,
        source="deterministic",
        description=description,
        evidence=evidence,
        native_element_ids=native_element_ids or [],
    )


def _model_hash(model: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
