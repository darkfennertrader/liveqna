from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


BlockKind = Literal[
    "heading",
    "paragraph",
    "table",
    "table_header",
    "table_row",
    "slide_note",
    "transcript_turn",
]

SlideElementKind = Literal[
    "title",
    "text",
    "table",
    "chart",
    "diagram",
    "image",
    "notes",
    "other",
]

SlideReviewCategory = Literal[
    "ATOMIC_FACT_CONFLICT",
    "INVENTED_RELATIONSHIP",
    "MISSING_KEY_ELEMENT",
    "PARTIAL_OR_UNCERTAIN",
    "FORMAT_ONLY",
    "NO_DISCREPANCY",
]

SlideReviewSeverity = Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
SlideReviewStatus = Literal["AUTO_PASS", "REVIEW_REQUIRED"]


class SourceLocator(BaseModel):
    page: int | None = None
    slide: int | None = None
    worksheet: str | None = None
    cell_range: str | None = None
    paragraph: int | None = None
    table: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    timestamp: str | None = None
    speaker: str | None = None


class NormalizedBlock(BaseModel):
    id: str
    kind: BlockKind
    text: str
    section_path: list[str] = Field(default_factory=list)
    source: SourceLocator


class ExtractionQuality(BaseModel):
    status: Literal["PASS", "WARNING"]
    block_count: int
    text_character_count: int
    warnings: list[str] = Field(default_factory=list)


class SlideDiscrepancy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    material: bool


class SlideBoundingBox(BaseModel):
    """Element bounds normalized to a 0-1000 slide coordinate space."""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)
    width: int = Field(ge=0, le=1000)
    height: int = Field(ge=0, le=1000)


class SlideVisualElement(BaseModel):
    """One region-level evidence item returned from the rendered slide."""

    model_config = ConfigDict(extra="forbid")

    kind: SlideElementKind
    content: str
    bbox: SlideBoundingBox | None


class SlideNativeElement(BaseModel):
    """One element extracted directly from the PPTX object model."""

    model_config = ConfigDict(extra="forbid")

    element_id: str
    kind: SlideElementKind
    content: str
    bbox: SlideBoundingBox | None = None
    block_ids: list[str] = Field(default_factory=list)


class SlideEvidenceAlignment(BaseModel):
    """Auditable relationship between native and rendered-slide evidence."""

    model_config = ConfigDict(extra="forbid")

    native_element_ids: list[str] = Field(default_factory=list)
    visual_element_ids: list[str] = Field(default_factory=list)
    relationship: Literal["duplicate", "overlap", "complement", "conflict"]
    text_coverage: float = Field(ge=0, le=1)
    spatial_overlap: float = Field(ge=0, le=1)
    spatial_similarity: float = Field(default=0, ge=0, le=1)
    type_compatibility: float = Field(default=0, ge=0, le=1)
    reading_order_similarity: float = Field(default=0, ge=0, le=1)
    score: float = Field(default=0, ge=0, le=1)
    match_method: Literal[
        "global", "global_grouped", "secondary", "unmatched", "model_reported"
    ] = "unmatched"
    reason: str


class SlideReconciliation(BaseModel):
    """Derived retrieval view; raw native and visual evidence remain intact."""

    model_config = ConfigDict(extra="forbid")

    version: str = "element-fusion-v3"
    alignments: list[SlideEvidenceAlignment] = Field(default_factory=list)
    suppressed_duplicate_visual_ids: list[str] = Field(default_factory=list)
    quarantined_conflict_visual_ids: list[str] = Field(default_factory=list)
    included_visual_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    retrieval_markdown: str


class SlideVisionResult(BaseModel):
    """Strict provider-neutral output for one rendered slide."""

    model_config = ConfigDict(extra="forbid")

    transcription_markdown: str
    tables_markdown: list[str] = Field(default_factory=list)
    visual_description: str
    elements: list[SlideVisualElement] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    discrepancies: list[SlideDiscrepancy] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class SlideVisionProvenance(BaseModel):
    provider: str
    model: str
    prompt_version: str
    response_hash: str
    duration_ms: int = Field(ge=0)


class SlideReviewFinding(BaseModel):
    """One structured discrepancy reported by the independent review call."""

    model_config = ConfigDict(extra="forbid")

    category: SlideReviewCategory
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    native_element_ids: list[str] = Field(default_factory=list)
    visual_element_ids: list[str] = Field(default_factory=list)


class SlideReviewResult(BaseModel):
    """Strict provider-neutral output from the second multimodal call."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    findings: list[SlideReviewFinding] = Field(min_length=1)


class SlideReviewProvenance(BaseModel):
    provider: str
    model: str
    prompt_version: str
    response_hash: str
    duration_ms: int = Field(ge=0)


class SlideReviewDecisionFinding(BaseModel):
    """A judge or deterministic finding with code-assigned severity."""

    model_config = ConfigDict(extra="forbid")

    category: SlideReviewCategory
    severity: SlideReviewSeverity
    source: Literal["deterministic", "judge", "system"]
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    native_element_ids: list[str] = Field(default_factory=list)
    visual_element_ids: list[str] = Field(default_factory=list)


class SlideReviewDecision(BaseModel):
    """Deterministic Gate 2 decision derived from evidence and judge output."""

    model_config = ConfigDict(extra="forbid")

    version: str = "slide-review-v1"
    status: SlideReviewStatus
    severity: SlideReviewSeverity
    findings: list[SlideReviewDecisionFinding] = Field(default_factory=list)
    judge_result: SlideReviewResult | None = None
    judge_provenance: SlideReviewProvenance | None = None


SlideRelationshipType = Literal[
    "previous",
    "next",
    "continues",
    "continued_from",
    "same_section",
    "same_topic",
    "references",
    "referenced_by",
]


class SlideRelationshipSignal(BaseModel):
    """One explainable contribution to a slide-relationship confidence."""

    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "slide_order",
        "explicit_reference",
        "adjacency",
        "title_similarity",
        "shared_entities",
        "label_similarity",
        "content_similarity",
    ]
    value: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=1)


class SlideRelationship(BaseModel):
    """A typed, auditable link from one normalized slide to another."""

    model_config = ConfigDict(extra="forbid")

    target_slide: int = Field(ge=1)
    type: SlideRelationshipType
    confidence: float = Field(ge=0, le=1)
    method: Literal["deterministic", "weighted_signals"]
    linker_version: str = "slide-linker-v1"
    evidence: str
    signals: list[SlideRelationshipSignal] = Field(default_factory=list)


class TranscriptMetadata(BaseModel):
    video_name: str | None = None
    time_range_start: str | None = None
    time_range_end: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class NormalizedPage(BaseModel):
    page_number: int = Field(ge=1)
    native_block_ids: list[str] = Field(default_factory=list)
    native_markdown: str
    selection_reasons: list[str] = Field(default_factory=list)
    native_text_character_count: int = Field(ge=0)
    max_image_pixels: int = Field(ge=0)
    vector_operation_count: int = Field(ge=0)
    image_path: str
    image_hash: str
    render_version: str
    vision: SlideVisionResult
    vision_provenance: SlideVisionProvenance
    combined_markdown: str


class NormalizedSlide(BaseModel):
    slide_number: int = Field(ge=1)
    title: str | None = None
    native_block_ids: list[str] = Field(default_factory=list)
    native_markdown: str
    native_elements: list[SlideNativeElement] = Field(default_factory=list)
    speaker_notes: str | None = None
    image_path: str
    image_hash: str
    render_version: str
    vision: SlideVisionResult
    vision_provenance: SlideVisionProvenance
    reconciliation: SlideReconciliation | None = None
    review: SlideReviewDecision | None = None
    relationships: list[SlideRelationship] = Field(default_factory=list)
    combined_markdown: str


class NormalizedDocument(BaseModel):
    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"] = "1.5"
    source_filename: str
    source_format: str
    content_hash: str
    blocks: list[NormalizedBlock]
    pages: list[NormalizedPage] = Field(default_factory=list)
    slides: list[NormalizedSlide] = Field(default_factory=list)
    transcript_metadata: TranscriptMetadata | None = None
    content_profile: str | None = None
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    llm_metadata: dict[str, Any] = Field(default_factory=dict)
    quality: ExtractionQuality
