from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from scipy.optimize import linear_sum_assignment

from .models import (
    NormalizedSlide,
    PageNativeElement,
    SlideBoundingBox,
    SlideEvidenceAlignment,
    SlideNativeElement,
    SlideReconciliation,
    SlideVisionResult,
    SlideVisualElement,
    WorksheetNativeElement,
)


RECONCILIATION_VERSION = "element-fusion-v3"
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_NUMBER = re.compile(
    r"(?<!\w)(?:[€$£]\s*)?[-+]?\d+(?:[.,]\d+)*(?:\s*%)?(?!\w)"
)
_TEXT_KINDS = {"title", "text", "table"}
_VISUAL_KINDS = {"chart", "diagram", "image"}
_MIN_GLOBAL_SCORE = 0.42


@dataclass(frozen=True)
class _PairMetrics:
    text: float
    iou: float
    spatial: float
    kind: float
    order: float
    score: float
    eligible: bool


def reconcile_slide(
    *,
    native_markdown: str,
    native_elements: list[SlideNativeElement],
    speaker_notes: str | None,
    vision: SlideVisionResult,
) -> SlideReconciliation:
    """Globally align native and rendered evidence without altering either."""

    return _reconcile_elements(
        native_markdown=native_markdown,
        native_elements=native_elements,
        supplementary_markdown=speaker_notes,
        vision=vision,
        version=RECONCILIATION_VERSION,
    )


def reconcile_page(
    *,
    native_markdown: str,
    native_elements: list[PageNativeElement],
    vision: SlideVisionResult,
) -> SlideReconciliation:
    """Reconcile one PDF page using the same evidence rules as a slide."""

    return _reconcile_elements(
        native_markdown=native_markdown,
        native_elements=native_elements,
        supplementary_markdown=None,
        vision=vision,
        version="pdf-element-fusion-v1",
    )


def reconcile_worksheet(
    *,
    native_markdown: str,
    native_elements: list[WorksheetNativeElement],
    vision: SlideVisionResult,
) -> SlideReconciliation:
    """Reconcile one rendered worksheet while retaining native cell authority."""

    return _reconcile_elements(
        native_markdown=native_markdown,
        native_elements=native_elements,
        supplementary_markdown=None,
        vision=vision,
        version="xlsx-element-fusion-v1",
    )


def _reconcile_elements(
    *,
    native_markdown: str,
    native_elements: (
        list[SlideNativeElement]
        | list[PageNativeElement]
        | list[WorksheetNativeElement]
    ),
    supplementary_markdown: str | None,
    vision: SlideVisionResult,
    version: str,
) -> SlideReconciliation:

    if not native_elements and native_markdown.strip():
        native_elements = [
            SlideNativeElement(
                element_id="native-slide-text",
                kind="text",
                content=native_markdown.strip(),
            )
        ]

    native_candidates = [item for item in native_elements if item.kind != "notes"]
    visual_elements = _visual_elements(vision)
    metrics = _pair_metrics(native_candidates, visual_elements)
    primary_matches = _global_matches(native_candidates, visual_elements, metrics)
    visual_to_native = {
        visual_index: [native_index]
        for native_index, visual_index in primary_matches
    }
    used_native = {native_index for native_index, _ in primary_matches}

    # A single visual text region may cover several native text boxes. Assign
    # every still-unmatched native fragment to its best eligible visual anchor.
    for native_index, native in enumerate(native_candidates):
        if native_index in used_native or native.kind not in _TEXT_KINDS:
            continue
        group_candidates: list[tuple[float, float, int]] = []
        for visual_index in visual_to_native:
            visual = visual_elements[visual_index][1]
            if visual.kind not in _TEXT_KINDS:
                continue
            pair = metrics[native_index][visual_index]
            if _belongs_to_visual_group(visual, native, pair):
                group_candidates.append((pair.text, pair.spatial, visual_index))
        if group_candidates:
            _, _, best_visual_index = max(group_candidates)
            visual_to_native[best_visual_index].append(native_index)
            used_native.add(native_index)

    alignments: list[SlideEvidenceAlignment] = []
    suppressed: list[str] = []
    quarantined: list[str] = []
    included: list[str] = []
    included_elements: list[tuple[str, SlideVisualElement]] = []
    conflicts: list[str] = []

    for visual_index, (visual_id, visual) in enumerate(visual_elements):
        native_indexes = visual_to_native.get(visual_index, [])
        match_method = "global"

        if not native_indexes and visual.kind in _TEXT_KINDS:
            secondary = _secondary_match(visual_index, native_candidates, metrics)
            if secondary is not None:
                native_indexes = [secondary]
                match_method = "secondary"
        elif len(native_indexes) > 1:
            match_method = "global_grouped"

        matched_native = [native_candidates[index] for index in native_indexes]
        aggregate = _aggregate_metrics(
            visual_index, visual, matched_native, native_indexes, metrics
        )
        conflict = _deterministic_conflict(visual, matched_native)

        if conflict:
            relationship = "conflict"
            reason = conflict
            conflicts.append(conflict)
            # Native extraction is authoritative for exact wording and numbers.
            # Keep conflicting visual evidence in the audit alignment, but never
            # place it in retrieval Markdown.
            quarantined.append(visual_id)
        elif visual.kind in _TEXT_KINDS and matched_native and aggregate.text >= 0.86:
            relationship = "duplicate"
            reason = (
                "Visual wording is already represented by the globally aligned "
                "native element group."
            )
            suppressed.append(visual_id)
        elif visual.kind in _TEXT_KINDS and matched_native:
            relationship = "overlap"
            reason = (
                "Visual wording partially overlaps the globally aligned native "
                "evidence and is retained to preserve unmatched meaning."
            )
            _include(visual_id, visual, included, included_elements)
        else:
            relationship = "complement"
            reason = (
                "Rendered-slide evidence adds visual meaning or wording not "
                "represented by a reliable native match."
            )
            _include(visual_id, visual, included, included_elements)
            if not matched_native:
                match_method = "unmatched"

        alignments.append(
            SlideEvidenceAlignment(
                native_element_ids=[item.element_id for item in matched_native],
                visual_element_ids=[visual_id],
                relationship=relationship,
                text_coverage=aggregate.text,
                spatial_overlap=aggregate.iou,
                spatial_similarity=aggregate.spatial,
                type_compatibility=aggregate.kind,
                reading_order_similarity=aggregate.order,
                score=aggregate.score,
                match_method=match_method,
                reason=reason,
            )
        )

    for discrepancy in vision.discrepancies:
        if not discrepancy.material or discrepancy.description in conflicts:
            continue
        conflicts.append(discrepancy.description)
        alignments.append(
            SlideEvidenceAlignment(
                relationship="conflict",
                text_coverage=0,
                spatial_overlap=0,
                match_method="model_reported",
                reason=discrepancy.description,
            )
        )

    return SlideReconciliation(
        version=version,
        alignments=alignments,
        suppressed_duplicate_visual_ids=suppressed,
        quarantined_conflict_visual_ids=quarantined,
        included_visual_ids=included,
        conflicts=conflicts,
        retrieval_markdown=_retrieval_markdown(
            native_markdown=native_markdown,
            included_visual_elements=included_elements,
            labels=vision.labels,
            speaker_notes=supplementary_markdown,
        ),
    )


def reconcile_existing_slide(slide: NormalizedSlide) -> NormalizedSlide:
    """Upgrade previously stored slide evidence without another model call."""

    native_elements = slide.native_elements
    if not native_elements and slide.native_markdown.strip():
        native_elements = [
            SlideNativeElement(
                element_id="native-slide-text",
                kind="text",
                content=slide.native_markdown.strip(),
                block_ids=slide.native_block_ids,
            )
        ]
    reconciliation = reconcile_slide(
        native_markdown=slide.native_markdown,
        native_elements=native_elements,
        speaker_notes=slide.speaker_notes,
        vision=slide.vision,
    )
    return slide.model_copy(
        update={
            "native_elements": native_elements,
            "reconciliation": reconciliation,
            "combined_markdown": reconciliation.retrieval_markdown,
        }
    )


def _visual_elements(
    vision: SlideVisionResult,
) -> list[tuple[str, SlideVisualElement]]:
    if vision.elements:
        return [
            (f"visual-element-{index}", element)
            for index, element in enumerate(vision.elements, start=1)
        ]

    # Compatibility path for stored prompt-v2 responses.
    elements: list[tuple[str, SlideVisualElement]] = []
    if vision.transcription_markdown.strip():
        elements.append(
            (
                "visual-transcription",
                SlideVisualElement(
                    kind="text",
                    content=vision.transcription_markdown.strip(),
                    bbox=None,
                ),
            )
        )
    elements.extend(
        (
            f"visual-table-{index}",
            SlideVisualElement(kind="table", content=table.strip(), bbox=None),
        )
        for index, table in enumerate(vision.tables_markdown, start=1)
        if table.strip()
    )
    if vision.visual_description.strip():
        elements.append(
            (
                "visual-description",
                SlideVisualElement(
                    kind="diagram",
                    content=vision.visual_description.strip(),
                    bbox=None,
                ),
            )
        )
    return elements


def _pair_metrics(
    native_elements: list[SlideNativeElement],
    visual_elements: list[tuple[str, SlideVisualElement]],
) -> list[list[_PairMetrics]]:
    native_order = _reading_order([item.bbox for item in native_elements])
    visual_order = _reading_order([item.bbox for _, item in visual_elements])
    rows: list[list[_PairMetrics]] = []
    for native_index, native in enumerate(native_elements):
        row: list[_PairMetrics] = []
        for visual_index, (_, visual) in enumerate(visual_elements):
            text = _text_similarity(visual.content, native.content)
            iou = _intersection_over_union(visual.bbox, native.bbox)
            spatial = _spatial_similarity(visual.bbox, native.bbox)
            kind = _kind_compatibility(visual.kind, native.kind)
            order = _order_similarity(
                native_order[native_index],
                visual_order[visual_index],
                len(native_elements),
                len(visual_elements),
            )
            textual = visual.kind in _TEXT_KINDS
            if textual:
                score = 0.55 * text + 0.25 * spatial + 0.15 * kind + 0.05 * order
                eligible = kind >= 0.6 and (text >= 0.30 or spatial >= 0.50)
            else:
                score = 0.10 * text + 0.50 * spatial + 0.30 * kind + 0.10 * order
                eligible = kind >= 0.55 and spatial >= 0.28
            row.append(
                _PairMetrics(
                    text=round(text, 4),
                    iou=round(iou, 4),
                    spatial=round(spatial, 4),
                    kind=round(kind, 4),
                    order=round(order, 4),
                    score=round(score if eligible else 0.0, 4),
                    eligible=eligible,
                )
            )
        rows.append(row)
    return rows


def _global_matches(
    native_elements: list[SlideNativeElement],
    visual_elements: list[tuple[str, SlideVisualElement]],
    metrics: list[list[_PairMetrics]],
) -> list[tuple[int, int]]:
    if not native_elements or not visual_elements:
        return []
    row_indexes, column_indexes = linear_sum_assignment(
        [[pair.score for pair in row] for row in metrics], maximize=True
    )
    matches = []
    for native_index, visual_index in zip(row_indexes, column_indexes, strict=True):
        pair = metrics[native_index][visual_index]
        if pair.eligible and pair.score >= _MIN_GLOBAL_SCORE:
            matches.append((int(native_index), int(visual_index)))
    return matches


def _belongs_to_visual_group(
    visual: SlideVisualElement,
    native: SlideNativeElement,
    pair: _PairMetrics,
) -> bool:
    if pair.kind < 0.6:
        return False
    contained = _intersection_over_smaller(visual.bbox, native.bbox) >= 0.75
    return pair.text >= 0.40 or (contained and pair.text >= 0.18)


def _secondary_match(
    visual_index: int,
    native_elements: list[SlideNativeElement],
    metrics: list[list[_PairMetrics]],
) -> int | None:
    ranked = sorted(
        (
            (row[visual_index].text, row[visual_index].spatial, index)
            for index, row in enumerate(metrics)
            if native_elements[index].kind in _TEXT_KINDS
        ),
        reverse=True,
    )
    if not ranked:
        return None
    text, spatial, native_index = ranked[0]
    if text >= 0.84 and (spatial >= 0.20 or native_elements[native_index].bbox is None):
        return native_index
    return None


def _aggregate_metrics(
    visual_index: int,
    visual: SlideVisualElement,
    matched_native: list[SlideNativeElement],
    native_indexes: list[int],
    metrics: list[list[_PairMetrics]],
) -> _PairMetrics:
    if not native_indexes:
        return _PairMetrics(0, 0, 0, 0, 0, 0, False)
    pairs = [metrics[index][visual_index] for index in native_indexes]
    ordered_native = sorted(
        matched_native,
        key=lambda item: (
            item.bbox.y if item.bbox else 1001,
            item.bbox.x if item.bbox else 1001,
            item.element_id,
        ),
    )
    aggregate_text = "\n\n".join(item.content for item in ordered_native)
    text = _text_similarity(visual.content, aggregate_text)
    return _PairMetrics(
        text=round(text, 4),
        iou=max(pair.iou for pair in pairs),
        spatial=max(pair.spatial for pair in pairs),
        kind=max(pair.kind for pair in pairs),
        order=max(pair.order for pair in pairs),
        score=max(pair.score for pair in pairs),
        eligible=True,
    )


def _deterministic_conflict(
    visual: SlideVisualElement, matched_native: list[SlideNativeElement]
) -> str | None:
    if visual.kind not in _TEXT_KINDS or not matched_native:
        return None
    native_text = "\n".join(item.content for item in matched_native)
    native_numbers = _numbers(native_text)
    visual_numbers = _numbers(visual.content)
    if native_numbers and visual_numbers and native_numbers != visual_numbers:
        native_ids = ", ".join(item.element_id for item in matched_native)
        return (
            f"Numeric mismatch for native element(s) {native_ids}: native "
            f"{sorted(native_numbers)} versus visual {sorted(visual_numbers)}"
        )
    return None


def _include(
    visual_id: str,
    visual: SlideVisualElement,
    included: list[str],
    included_elements: list[tuple[str, SlideVisualElement]],
) -> None:
    included.append(visual_id)
    included_elements.append((visual_id, visual))


def _text_similarity(left: str, right: str) -> float:
    left_normalized = _normalized_text(left)
    right_normalized = _normalized_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    left_tokens = _tokens(left_normalized)
    right_tokens = _tokens(right_normalized)
    token_f1 = _counter_f1(Counter(left_tokens), Counter(right_tokens))
    left_bigrams = Counter(zip(left_tokens, left_tokens[1:]))
    right_bigrams = Counter(zip(right_tokens, right_tokens[1:]))
    bigram_f1 = _counter_f1(left_bigrams, right_bigrams)
    token_coverage = _counter_coverage(Counter(left_tokens), Counter(right_tokens))
    bigram_coverage = _counter_coverage(left_bigrams, right_bigrams)
    return round(
        max(
            sequence,
            0.65 * token_f1 + 0.35 * bigram_f1,
            0.65 * token_coverage + 0.35 * bigram_coverage,
        ),
        4,
    )


def _counter_f1(left: Counter, right: Counter) -> float:
    if not left or not right:
        return 0.0
    overlap = sum((left & right).values())
    precision = overlap / sum(left.values())
    recall = overlap / sum(right.values())
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _counter_coverage(candidate: Counter, authority: Counter) -> float:
    if not candidate or not authority:
        return 0.0
    return sum((candidate & authority).values()) / sum(candidate.values())


def _normalized_text(value: str) -> str:
    return " ".join(_tokens(value))


def _tokens(value: str) -> list[str]:
    return [item.casefold() for item in _TOKEN.findall(value)]


def _numbers(value: str) -> set[str]:
    return {
        _normalize_number(match.group(0)) for match in _NUMBER.finditer(value)
    }


def _normalize_number(value: str) -> str:
    compact = re.sub(r"\s+", "", value).casefold()
    currency = compact[0] if compact and compact[0] in "€$£" else ""
    percent = "%" if compact.endswith("%") else ""
    numeric = compact.removeprefix(currency).removesuffix(percent)
    if re.fullmatch(r"[-+]?\d{1,3}(?:[,.]\d{3})+", numeric):
        numeric = numeric.replace(",", "").replace(".", "")
    elif "," in numeric and "." in numeric:
        decimal_separator = "," if numeric.rfind(",") > numeric.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        numeric = numeric.replace(thousands_separator, "").replace(
            decimal_separator, "."
        )
    else:
        numeric = numeric.replace(",", ".")
    try:
        numeric = format(Decimal(numeric).normalize(), "f")
    except InvalidOperation:
        pass
    return f"{currency}{numeric}{percent}"


def _kind_compatibility(visual_kind: str, native_kind: str) -> float:
    if visual_kind == native_kind:
        return 1.0
    if {visual_kind, native_kind} <= {"title", "text"}:
        return 0.9
    if visual_kind in _VISUAL_KINDS and native_kind in _VISUAL_KINDS:
        return 0.72
    if visual_kind == "diagram" and native_kind == "text":
        return 0.25
    return 0.0


def _reading_order(boxes: list[SlideBoundingBox | None]) -> list[int]:
    ranked = sorted(
        range(len(boxes)),
        key=lambda index: (
            boxes[index].y if boxes[index] else 1001,
            boxes[index].x if boxes[index] else 1001,
            index,
        ),
    )
    result = [0] * len(boxes)
    for rank, index in enumerate(ranked):
        result[index] = rank
    return result


def _order_similarity(
    native_rank: int,
    visual_rank: int,
    native_count: int,
    visual_count: int,
) -> float:
    native_position = native_rank / max(1, native_count - 1)
    visual_position = visual_rank / max(1, visual_count - 1)
    return max(0.0, 1.0 - abs(native_position - visual_position))


def _intersection(
    left: SlideBoundingBox | None, right: SlideBoundingBox | None
) -> float:
    if left is None or right is None:
        return 0.0
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    return max(0, x2 - x1) * max(0, y2 - y1)


def _intersection_over_union(
    left: SlideBoundingBox | None, right: SlideBoundingBox | None
) -> float:
    intersection = _intersection(left, right)
    if left is None or right is None:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union else 0.0


def _intersection_over_smaller(
    left: SlideBoundingBox | None, right: SlideBoundingBox | None
) -> float:
    intersection = _intersection(left, right)
    if left is None or right is None:
        return 0.0
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection / smaller if smaller else 0.0


def _spatial_similarity(
    left: SlideBoundingBox | None, right: SlideBoundingBox | None
) -> float:
    if left is None or right is None:
        return 0.0
    iou = _intersection_over_union(left, right)
    containment = _intersection_over_smaller(left, right)
    left_center = (left.x + left.width / 2, left.y + left.height / 2)
    right_center = (right.x + right.width / 2, right.y + right.height / 2)
    distance = math.dist(left_center, right_center)
    proximity = max(0.0, 1.0 - distance / math.sqrt(2_000_000))
    return min(1.0, max(iou, 0.95 * containment, 0.25 * proximity))


def _retrieval_markdown(
    *,
    native_markdown: str,
    included_visual_elements: list[tuple[str, SlideVisualElement]],
    labels: list[str],
    speaker_notes: str | None,
) -> str:
    sections: list[str] = []
    if native_markdown.strip():
        sections.append(native_markdown.strip())

    visual_tables = [
        element.content.strip()
        for _, element in included_visual_elements
        if element.kind == "table" and element.content.strip()
    ]
    if visual_tables:
        sections.append(
            "## Unmatched visual tables\n\n" + "\n\n".join(visual_tables)
        )

    visual_text = [
        element.content.strip()
        for _, element in included_visual_elements
        if element.kind in {"title", "text"} and element.content.strip()
    ]
    if visual_text:
        sections.append(
            "## Unmatched visual text\n\n" + "\n\n".join(visual_text)
        )

    visual_meaning = [
        element.content.strip()
        for _, element in included_visual_elements
        if element.kind not in {"title", "text", "table"}
        and element.content.strip()
    ]
    if visual_meaning:
        sections.append("## Visual evidence\n\n" + "\n\n".join(visual_meaning))

    unique_labels = list(
        dict.fromkeys(label.strip() for label in labels if label.strip())
    )
    if unique_labels:
        sections.append("## Retrieval labels\n\n" + ", ".join(unique_labels))
    if speaker_notes:
        sections.append("## Speaker notes\n\n" + speaker_notes.strip())
    return "\n\n".join(sections).strip()
