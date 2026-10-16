from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BlockKind = Literal[
    "heading",
    "paragraph",
    "list",
    "blockquote",
    "code",
    "metadata",
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
    story: str | None = None
    part: str | None = None
    section: int | None = None
    row: int | None = None
    column: int | None = None


class NormalizedBlock(BaseModel):
    id: str
    kind: BlockKind
    text: str
    section_path: list[str] = Field(default_factory=list)
    source: SourceLocator
    retrieval_eligible: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)


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


class DocumentNativeElement(BaseModel):
    """One positioned element extracted directly from a source document."""

    model_config = ConfigDict(extra="forbid")

    element_id: str
    kind: SlideElementKind
    content: str
    bbox: SlideBoundingBox | None = None
    block_ids: list[str] = Field(default_factory=list)
    reading_order: int | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


# Format-specific aliases keep the public schema readable while allowing the
# reconciliation engine to operate on the same evidence contract.
SlideNativeElement = DocumentNativeElement
PageNativeElement = DocumentNativeElement
WorksheetNativeElement = DocumentNativeElement


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


class SlideResolutionResult(BaseModel):
    """Strict LLM proposal grounded in one human answer."""

    model_config = ConfigDict(extra="forbid")

    resolved_markdown: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    remaining_uncertainty: bool
    follow_up_question: str | None = None


class SlideHumanResolution(BaseModel):
    """Auditable human answer and its LLM-produced normalized proposal."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    finding_index: int = Field(ge=0)
    question: str
    answer: str
    status: Literal["PROPOSED", "APPLIED"] = "PROPOSED"
    resolved_markdown: str
    rationale: str
    remaining_uncertainty: bool
    follow_up_question: str | None = None
    provider: str
    model: str
    prompt_version: str
    response_hash: str
    duration_ms: int = Field(ge=0)


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

PageRelationshipType = Literal[
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
        "page_order",
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


class PageRelationship(BaseModel):
    """A typed, auditable link from one normalized PDF page to another."""

    model_config = ConfigDict(extra="forbid")

    target_page: int = Field(ge=1)
    type: PageRelationshipType
    confidence: float = Field(ge=0, le=1)
    method: Literal["deterministic", "weighted_signals"]
    linker_version: str = "page-linker-v1"
    evidence: str
    signals: list[SlideRelationshipSignal] = Field(default_factory=list)


class TranscriptMetadata(BaseModel):
    video_name: str | None = None
    time_range_start: str | None = None
    time_range_end: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class DocxMetadata(BaseModel):
    """Package and native WordprocessingML inventory retained for audit."""

    model_config = ConfigDict(extra="forbid")

    revision_policy: Literal["final", "original"]
    story_parts: list[str] = Field(default_factory=list)
    section_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    content_control_count: int = Field(ge=0)
    text_box_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    footnote_count: int = Field(ge=0)
    endnote_count: int = Field(ge=0)
    inserted_revision_count: int = Field(ge=0)
    deleted_revision_count: int = Field(ge=0)
    external_links: list[str] = Field(default_factory=list)
    embedded_objects: list[str] = Field(default_factory=list)
    core_properties: dict[str, Any] = Field(default_factory=dict)
    custom_properties: dict[str, Any] = Field(default_factory=dict)
    package_entry_count: int = Field(ge=0)
    package_compressed_bytes: int = Field(ge=0)
    package_uncompressed_bytes: int = Field(ge=0)
    package_compression_ratio: float = Field(ge=0)


class NormalizedPage(BaseModel):
    page_number: int = Field(ge=1)
    native_block_ids: list[str] = Field(default_factory=list)
    native_markdown: str
    native_elements: list[PageNativeElement] = Field(default_factory=list)
    selection_reasons: list[str] = Field(default_factory=list)
    native_text_character_count: int = Field(ge=0)
    max_image_pixels: int = Field(ge=0)
    vector_operation_count: int = Field(ge=0)
    image_path: str
    image_hash: str
    render_version: str
    vision: SlideVisionResult
    vision_provenance: SlideVisionProvenance
    reconciliation: SlideReconciliation | None = None
    review: SlideReviewDecision | None = None
    human_resolutions: list[SlideHumanResolution] = Field(default_factory=list)
    relationships: list[PageRelationship] = Field(default_factory=list)
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
    human_resolutions: list[SlideHumanResolution] = Field(default_factory=list)
    relationships: list[SlideRelationship] = Field(default_factory=list)
    combined_markdown: str


class WorkbookCell(BaseModel):
    """Auditable native and calculated evidence for one non-empty cell."""

    model_config = ConfigDict(extra="forbid")

    coordinate: str
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    value: Any = None
    data_type: str
    number_format: str | None = None
    formula: str | None = None
    cached_value: Any = None
    recalculated_value: Any = None
    formula_status: Literal[
        "NOT_FORMULA",
        "MATCH",
        "MISSING_CACHE",
        "VALUE_CHANGED",
        "ERROR",
        "NOT_RECALCULATED",
    ] = "NOT_FORMULA"
    merged_range: str | None = None


class WorksheetRegion(BaseModel):
    """One logical retrieval unit within a worksheet."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal[
        "excel_table",
        "named_range",
        "pivot_output",
        "detected_table",
        "remaining_cells",
    ]
    name: str | None = None
    cell_range: str
    header_row: int | None = Field(default=None, ge=1)
    columns: list[str] = Field(default_factory=list)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    block_ids: list[str] = Field(default_factory=list)
    native_markdown: str
    topics: list[str] = Field(default_factory=list)


class WorkbookDefinedName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    destinations: list[str] = Field(default_factory=list)
    expression: str | None = None
    scope: str | None = None


class WorkbookMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_system: Literal["1900", "1904"]
    calculation_mode: str | None = None
    defined_names: list[WorkbookDefinedName] = Field(default_factory=list)
    external_links: list[str] = Field(default_factory=list)
    embedded_objects: list[str] = Field(default_factory=list)
    package_entry_count: int = Field(ge=0)
    package_uncompressed_bytes: int = Field(ge=0)
    package_compression_ratio: float = Field(ge=0)


class NormalizedWorksheet(BaseModel):
    worksheet_index: int = Field(ge=1)
    title: str
    state: Literal["visible", "hidden", "veryHidden"]
    used_range: str | None = None
    freeze_panes: str | None = None
    auto_filter_range: str | None = None
    merged_ranges: list[str] = Field(default_factory=list)
    table_names: list[str] = Field(default_factory=list)
    chart_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    pivot_table_count: int = Field(ge=0)
    conditional_formatting_count: int = Field(ge=0)
    data_validation_count: int = Field(ge=0)
    cells: list[WorkbookCell] = Field(default_factory=list)
    regions: list[WorksheetRegion] = Field(default_factory=list)
    native_block_ids: list[str] = Field(default_factory=list)
    native_markdown: str
    native_elements: list[WorksheetNativeElement] = Field(default_factory=list)
    selection_reasons: list[str] = Field(default_factory=list)
    image_path: str | None = None
    image_hash: str | None = None
    rendered_page_count: int = Field(default=0, ge=0)
    render_version: str | None = None
    vision: SlideVisionResult | None = None
    vision_provenance: SlideVisionProvenance | None = None
    reconciliation: SlideReconciliation | None = None
    review: SlideReviewDecision | None = None
    human_resolutions: list[SlideHumanResolution] = Field(default_factory=list)
    combined_markdown: str


class NormalizedDocument(BaseModel):
    schema_version: Literal[
        "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"
    ] = "1.8"
    source_filename: str
    source_format: str
    content_hash: str
    blocks: list[NormalizedBlock]
    pages: list[NormalizedPage] = Field(default_factory=list)
    slides: list[NormalizedSlide] = Field(default_factory=list)
    worksheets: list[NormalizedWorksheet] = Field(default_factory=list)
    workbook_metadata: WorkbookMetadata | None = None
    docx_metadata: DocxMetadata | None = None
    transcript_metadata: TranscriptMetadata | None = None
    content_profile: str | None = None
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    llm_metadata: dict[str, Any] = Field(default_factory=dict)
    quality: ExtractionQuality
