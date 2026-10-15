from __future__ import annotations

import math
import re
from collections import Counter

from .models import (
    NormalizedSlide,
    SlideRelationship,
    SlideRelationshipSignal,
)


LINKER_VERSION = "slide-linker-v1"
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_EXPLICIT_REFERENCE = re.compile(
    r"\bslides?\s+(?:number\s+)?(?P<number>\d+)\b", re.IGNORECASE
)
_CAPITALIZED_TERM = re.compile(r"\b[A-Z][A-Za-z0-9&/-]{2,}\b")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}
_SAME_SECTION_THRESHOLD = 0.72
_CONTINUES_THRESHOLD = 0.65
_SAME_TOPIC_THRESHOLD = 0.68
_MAX_TOPIC_LINKS_PER_SLIDE = 3


def link_slides(slides: list[NormalizedSlide]) -> list[NormalizedSlide]:
    """Add reciprocal, explainable relationships before normalization ends."""

    if not slides:
        return []
    ordered = sorted(slides, key=lambda item: item.slide_number)
    slide_numbers = {slide.slide_number for slide in ordered}
    relationships: dict[int, list[SlideRelationship]] = {
        slide.slide_number: [] for slide in ordered
    }
    content_vectors = _tf_idf_vectors(ordered)

    for left, right in zip(ordered, ordered[1:]):
        if right.slide_number != left.slide_number + 1:
            continue
        _add_reciprocal(
            relationships,
            left.slide_number,
            right.slide_number,
            forward_type="next",
            inverse_type="previous",
            confidence=1.0,
            method="deterministic",
            evidence="Slides are consecutive in the source presentation.",
            signals=[_signal("slide_order", 1.0, 1.0)],
        )

    by_number = {slide.slide_number: slide for slide in ordered}
    for slide in ordered:
        source_text = _link_text(slide)
        for match in _EXPLICIT_REFERENCE.finditer(source_text):
            target = int(match.group("number"))
            if target not in slide_numbers or target == slide.slide_number:
                continue
            _add_reciprocal(
                relationships,
                slide.slide_number,
                target,
                forward_type="references",
                inverse_type="referenced_by",
                confidence=1.0,
                method="deterministic",
                evidence=f'Explicit source wording references slide {target}.',
                signals=[_signal("explicit_reference", 1.0, 1.0)],
            )

    topic_candidates: dict[int, list[tuple[float, int, SlideRelationship]]] = {
        slide.slide_number: [] for slide in ordered
    }
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            adjacent = float(right.slide_number == left.slide_number + 1)
            title = _jaccard(_title_tokens(left), _title_tokens(right))
            labels = _jaccard(_label_tokens(left), _label_tokens(right))
            entities = _jaccard(_entity_tokens(left), _entity_tokens(right))
            content = _cosine(
                content_vectors[left.slide_number],
                content_vectors[right.slide_number],
            )

            if adjacent:
                section_signals = [
                    _signal("adjacency", adjacent, 0.20),
                    _signal("title_similarity", title, 0.45),
                    _signal("label_similarity", labels, 0.35),
                ]
                section_score = _score(section_signals)
                if section_score >= _SAME_SECTION_THRESHOLD:
                    _add_symmetric(
                        relationships,
                        left.slide_number,
                        right.slide_number,
                        relationship_type="same_section",
                        confidence=section_score,
                        evidence=(
                            "Adjacent slides share title and retrieval-label "
                            "vocabulary."
                        ),
                        signals=section_signals,
                    )

                continues_signals = [
                    _signal("adjacency", adjacent, 0.30),
                    _signal("title_similarity", title, 0.25),
                    _signal("shared_entities", entities, 0.20),
                    _signal("content_similarity", content, 0.25),
                ]
                continues_score = _score(continues_signals)
                if continues_score >= _CONTINUES_THRESHOLD:
                    _add_reciprocal(
                        relationships,
                        left.slide_number,
                        right.slide_number,
                        forward_type="continues",
                        inverse_type="continued_from",
                        confidence=continues_score,
                        method="weighted_signals",
                        evidence=(
                            "Adjacent slides have sufficient title, entity, "
                            "and normalized-content continuity."
                        ),
                        signals=continues_signals,
                    )

            topic_signals = [
                _signal("title_similarity", title, 0.20),
                _signal("label_similarity", labels, 0.25),
                _signal("content_similarity", content, 0.55),
            ]
            topic_score = _score(topic_signals)
            if topic_score >= _SAME_TOPIC_THRESHOLD:
                left_link = _relationship(
                    target=right.slide_number,
                    relationship_type="same_topic",
                    confidence=topic_score,
                    method="weighted_signals",
                    evidence=(
                        "Slides share sufficient title, retrieval-label, and "
                        "normalized-content similarity."
                    ),
                    signals=topic_signals,
                )
                right_link = left_link.model_copy(
                    update={"target_slide": left.slide_number}
                )
                topic_candidates[left.slide_number].append(
                    (topic_score, right.slide_number, left_link)
                )
                topic_candidates[right.slide_number].append(
                    (topic_score, left.slide_number, right_link)
                )

    for slide_number, candidates in topic_candidates.items():
        ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
        relationships[slide_number].extend(
            relationship for _, _, relationship in ranked[:_MAX_TOPIC_LINKS_PER_SLIDE]
        )

    return [
        slide.model_copy(
            update={
                "relationships": _deduplicate_and_sort(
                    relationships[slide.slide_number]
                )
            }
        )
        for slide in ordered
    ]


def _link_text(slide: NormalizedSlide) -> str:
    return "\n".join(
        value
        for value in (
            slide.title or "",
            slide.native_markdown,
            slide.vision.transcription_markdown,
            slide.vision.visual_description,
            slide.speaker_notes or "",
        )
        if value
    )


def _content_tokens(slide: NormalizedSlide) -> list[str]:
    return [
        token
        for token in _tokens(slide.combined_markdown)
        if token not in _STOPWORDS and len(token) > 1
    ]


def _title_tokens(slide: NormalizedSlide) -> set[str]:
    return {
        token
        for token in _tokens(slide.title or "")
        if token not in _STOPWORDS and len(token) > 1
    }


def _label_tokens(slide: NormalizedSlide) -> set[str]:
    return {
        token
        for label in slide.vision.labels
        for token in _tokens(label)
        if token not in _STOPWORDS and len(token) > 1
    }


def _entity_tokens(slide: NormalizedSlide) -> set[str]:
    label_tokens = _label_tokens(slide)
    capitalized = {
        match.group(0).casefold()
        for match in _CAPITALIZED_TERM.finditer(_link_text(slide))
        if match.group(0).casefold() not in _STOPWORDS
    }
    return label_tokens | capitalized


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(value)]


def _tf_idf_vectors(slides: list[NormalizedSlide]) -> dict[int, dict[str, float]]:
    tokens_by_slide = {
        slide.slide_number: _content_tokens(slide) for slide in slides
    }
    document_frequency = Counter(
        token
        for tokens in tokens_by_slide.values()
        for token in set(tokens)
    )
    document_count = len(slides)
    vectors: dict[int, dict[str, float]] = {}
    for slide_number, tokens in tokens_by_slide.items():
        term_frequency = Counter(tokens)
        vectors[slide_number] = {
            token: count
            * (math.log((document_count + 1) / (document_frequency[token] + 1)) + 1)
            for token, count in term_frequency.items()
        }
    return vectors


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    shared = left.keys() & right.keys()
    numerator = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _signal(name: str, value: float, weight: float) -> SlideRelationshipSignal:
    bounded = min(1.0, max(0.0, value))
    return SlideRelationshipSignal(
        name=name,
        value=round(bounded, 4),
        weight=weight,
        contribution=round(bounded * weight, 4),
    )


def _score(signals: list[SlideRelationshipSignal]) -> float:
    return round(min(1.0, sum(item.contribution for item in signals)), 4)


def _relationship(
    *,
    target: int,
    relationship_type: str,
    confidence: float,
    method: str,
    evidence: str,
    signals: list[SlideRelationshipSignal],
) -> SlideRelationship:
    return SlideRelationship(
        target_slide=target,
        type=relationship_type,
        confidence=confidence,
        method=method,
        evidence=evidence,
        signals=signals,
    )


def _add_reciprocal(
    relationships: dict[int, list[SlideRelationship]],
    source: int,
    target: int,
    *,
    forward_type: str,
    inverse_type: str,
    confidence: float,
    method: str,
    evidence: str,
    signals: list[SlideRelationshipSignal],
) -> None:
    relationships[source].append(
        _relationship(
            target=target,
            relationship_type=forward_type,
            confidence=confidence,
            method=method,
            evidence=evidence,
            signals=signals,
        )
    )
    relationships[target].append(
        _relationship(
            target=source,
            relationship_type=inverse_type,
            confidence=confidence,
            method=method,
            evidence=evidence,
            signals=signals,
        )
    )


def _add_symmetric(
    relationships: dict[int, list[SlideRelationship]],
    left: int,
    right: int,
    *,
    relationship_type: str,
    confidence: float,
    evidence: str,
    signals: list[SlideRelationshipSignal],
) -> None:
    _add_reciprocal(
        relationships,
        left,
        right,
        forward_type=relationship_type,
        inverse_type=relationship_type,
        confidence=confidence,
        method="weighted_signals",
        evidence=evidence,
        signals=signals,
    )


def _deduplicate_and_sort(
    relationships: list[SlideRelationship],
) -> list[SlideRelationship]:
    unique: dict[tuple[int, str], SlideRelationship] = {}
    for relationship in relationships:
        key = (relationship.target_slide, relationship.type)
        current = unique.get(key)
        if current is None or relationship.confidence > current.confidence:
            unique[key] = relationship
    return sorted(
        unique.values(),
        key=lambda item: (item.target_slide, item.type),
    )
