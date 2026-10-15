from __future__ import annotations

import re

from .models import NormalizedPage, PageRelationship, SlideRelationshipSignal


LINKER_VERSION = "page-linker-v1"
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_PAGE_REFERENCE = re.compile(r"\bpages?\s+(?P<number>\d+)\b", re.IGNORECASE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}


def link_pages(pages: list[NormalizedPage]) -> list[NormalizedPage]:
    """Add deterministic and explainable cross-page relationships."""

    ordered = sorted(pages, key=lambda item: item.page_number)
    relationships: dict[int, list[PageRelationship]] = {
        page.page_number: [] for page in ordered
    }
    available = set(relationships)

    for left, right in zip(ordered, ordered[1:]):
        if right.page_number != left.page_number + 1:
            continue
        _reciprocal(
            relationships,
            left.page_number,
            right.page_number,
            "next",
            "previous",
            1.0,
            "Pages are consecutive in the source PDF.",
            [_signal("page_order", 1.0, 1.0)],
        )
        if _table_continues(left, right):
            _reciprocal(
                relationships,
                left.page_number,
                right.page_number,
                "continues",
                "continued_from",
                0.9,
                "A native table crosses the consecutive page boundary.",
                [_signal("adjacency", 1.0, 1.0)],
            )

    for page in ordered:
        for match in _PAGE_REFERENCE.finditer(page.native_markdown):
            target = int(match.group("number"))
            if target in available and target != page.page_number:
                _reciprocal(
                    relationships,
                    page.page_number,
                    target,
                    "references",
                    "referenced_by",
                    1.0,
                    f"Native source wording explicitly references page {target}.",
                    [_signal("explicit_reference", 1.0, 1.0)],
                )

    for index, left in enumerate(ordered):
        candidates: list[tuple[float, NormalizedPage]] = []
        left_tokens = _semantic_tokens(left)
        for right in ordered[index + 1 :]:
            similarity = _jaccard(left_tokens, _semantic_tokens(right))
            if similarity >= 0.45:
                candidates.append((similarity, right))
        for similarity, right in sorted(candidates, key=lambda item: -item[0])[:3]:
            _reciprocal(
                relationships,
                left.page_number,
                right.page_number,
                "same_topic",
                "same_topic",
                similarity,
                "Pages share sufficient native, visual, and retrieval-label vocabulary.",
                [_signal("content_similarity", similarity, 1.0)],
                method="weighted_signals",
            )

    return [
        page.model_copy(
            update={
                "relationships": _deduplicate(
                    relationships[page.page_number]
                )
            }
        )
        for page in ordered
    ]


def _table_continues(left: NormalizedPage, right: NormalizedPage) -> bool:
    left_tables = [
        item for item in left.native_elements
        if item.kind == "table" and item.bbox is not None
    ]
    right_tables = [
        item for item in right.native_elements
        if item.kind == "table" and item.bbox is not None
    ]
    return bool(
        left_tables
        and right_tables
        and max(item.bbox.y + item.bbox.height for item in left_tables) >= 800
        and min(item.bbox.y for item in right_tables) <= 250
    )


def _semantic_tokens(page: NormalizedPage) -> set[str]:
    values = [page.combined_markdown, *page.vision.labels]
    return {
        token.casefold()
        for value in values
        for token in _TOKEN.findall(value)
        if token.casefold() not in _STOPWORDS and len(token) > 2
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return round(len(left & right) / len(left | right), 4)


def _signal(name: str, value: float, weight: float) -> SlideRelationshipSignal:
    return SlideRelationshipSignal(
        name=name,
        value=value,
        weight=weight,
        contribution=round(value * weight, 4),
    )


def _reciprocal(
    relationships: dict[int, list[PageRelationship]],
    source: int,
    target: int,
    forward: str,
    inverse: str,
    confidence: float,
    evidence: str,
    signals: list[SlideRelationshipSignal],
    *,
    method: str = "deterministic",
) -> None:
    relationships[source].append(
        PageRelationship(
            target_page=target,
            type=forward,
            confidence=confidence,
            method=method,
            linker_version=LINKER_VERSION,
            evidence=evidence,
            signals=signals,
        )
    )
    relationships[target].append(
        PageRelationship(
            target_page=source,
            type=inverse,
            confidence=confidence,
            method=method,
            linker_version=LINKER_VERSION,
            evidence=evidence,
            signals=signals,
        )
    )


def _deduplicate(items: list[PageRelationship]) -> list[PageRelationship]:
    unique: dict[tuple[int, str], PageRelationship] = {}
    for item in items:
        key = (item.target_page, item.type)
        current = unique.get(key)
        if current is None or item.confidence > current.confidence:
            unique[key] = item
    return sorted(unique.values(), key=lambda item: (item.target_page, item.type))
