# LiveQnA ingestion service

> Status: Phases 0 and 1 are complete. The Phase 2 native and PPTX hybrid
> extraction implementation passes automated tests. Five representative slides
> from the real PoC template have completed both multimodal calls. Slides 5, 6,
> 14, and 24 passed automatically; slide 22 remains in Review Gate 2 because an
> overlay obscures part of the diagram. Chunking and later phases have not started.

## Purpose

The ingestion service prepares knowledge before a live call. It accepts project
documents, extracts and chunks their content, creates lexical and semantic
representations, and publishes searchable chunks to Qdrant.

The completed service will accept documents through the local LiveQnA
application UI. During development, the pipeline will first be exercised
through its internal API, CLI, and automated tests. UI integration happens only
after the independent ingestion and retrieval phases pass their review gates.
Embeddings remain local; the first implementation will not send document text
to a managed embedding API. PPTX slide images may be sent to a configured
vision-model provider only when that provider has been explicitly enabled.
The same provider interface must support a local vision model so deployments
can keep all document content local.

The corpus is English-only. The host is Ubuntu with an NVIDIA GPU providing 12
GB of VRAM, so the ingestion pipeline may use a larger English embedding model
through local GPU inference.

It also owns the deliberately small document lifecycle used by LiveQnA:

```text
DRAFT -> APPROVED -> ARCHIVED
```

The service must keep this lifecycle easy to understand:

- Every newly ingested version starts as `DRAFT`.
- A person explicitly promotes a draft version to `APPROVED`.
- Approving a new version automatically changes the previous approved version
  of the same document to `ARCHIVED`.
- `ARCHIVED` therefore means "previously approved, now superseded".
- A draft that should not be retained can be deleted; it does not become an
  archived version.
- Only one approved version of a logical document may exist within a project.

## Example workflow

The same lifecycle applies whether the material originated with the client,
internally, in a meeting, or as a produced artifact.

### Official documentation

```text
Receive official document -> ingest as DRAFT -> verify -> APPROVE
```

### Internal documentation validated with the client

```text
Ingest internal document as DRAFT -> discuss with client -> APPROVE
```

Approval applies to the exact version discussed with the client. The origin of
the document does not prevent it from becoming approved.

### Meetings

```text
Meeting transcript -> DRAFT
Validated meeting summary or decisions -> APPROVED
```

The raw transcript can assist preparation, but it is not automatically treated
as approved evidence. A reviewed meeting summary can be approved independently.

### Produced artifacts

```text
Artifact v1 DRAFT -> APPROVED
Artifact v2 DRAFT -> APPROVED
                     |
                     +-> v1 becomes ARCHIVED
```

## Retrieval policy

The lifecycle supports two retrieval modes:

| Mode | Eligible content |
| --- | --- |
| Preparation | `DRAFT` and `APPROVED` |
| Live Q&A | `APPROVED` only |

Archived content is excluded by default. Historical retrieval must be an
explicit operation.

If no suitable approved evidence exists during a live call, LiveQnA should
return a no-match/defer outcome instead of using draft or archived content.

## Proposed service boundary

The ingestion service will:

- register projects and logical documents;
- store immutable document versions;
- manage the three lifecycle states;
- extract text, structural information, and required visual meaning from
  supported formats;
- render PPTX files to stable per-slide images and enrich them through a
  configurable vision-model provider;
- create structure-aware chunks;
- generate dense semantic embeddings and sparse lexical representations;
- publish and update Qdrant points;
- expose document-management and ingestion-status operations;
- provide a test search operation for ingestion quality checks;
- preserve traceability to the original file location.

It will not:

- capture live audio or transcribe calls;
- generate live questions or answers;
- automatically approve documents;
- treat Qdrant as the authoritative document registry;
- use vector retrieval for authoritative calculations from spreadsheets.

## Proposed local architecture

```text
Browser on the local machine
        |
        v
LiveQnA UI -> Ingestion service -> Local document files
                         |
                         +-------> PostgreSQL
                         |         document registry, versions,
                         |         lifecycle and processing state
                         |
                         +-------> PPTX renderer
                         |         one stable PNG per slide
                         |                 |
                         |                 v
                         |         Vision-model provider
                         |         local or explicitly enabled remote
                         |
                         +-------> Local FastEmbed inference
                         |         dense and sparse embeddings
                         |
                         +-------> Qdrant
                                   vectors, chunk text and
                                   retrieval payload
```

The existing Qdrant container remains the retrieval index. A new PostgreSQL
container will be the source of truth for document identity, versions, status,
and ingestion processing.

Original files will initially use a local bind-mounted directory. Object
storage such as MinIO, S3, or Azure Blob can be introduced later without
changing the lifecycle.

The ingestion service will be reachable through the existing local UI service,
not exposed as a public document-upload endpoint. Authentication and multiple
approver identities are outside the first implementation because this is a
single-user local application.

## Logical file view

The service will present the following simple project view:

```text
project-name/
|-- draft/
|-- approved/
`-- archived/
```

Internally, files should be stored using stable project, document, and version
identifiers so that status changes do not break citations. PostgreSQL provides
the authoritative mapping from each immutable file version to its displayed
lifecycle location.

## PostgreSQL model

The first implementation should use only the tables necessary for ingestion
and version control.

### `projects`

- `id`
- `name`
- `created_at`

### `documents`

Represents a logical document across all of its versions.

- `id`
- `project_id`
- `title`
- `document_type`: `official`, `internal`, `transcript`, or `artifact`
- `content_profile`: optional business-use profile such as `qbr` or
  `general_presentation`
- `created_at`

### `document_versions`

- `id`
- `document_id`
- `version_label`
- `status`: `DRAFT`, `APPROVED`, or `ARCHIVED`
- `file_path`
- `content_hash`
- `source_filename`
- `created_at`
- `approved_at`
- `approved_by`
- `supersedes_version_id`

A partial unique constraint must ensure that only one version per document has
the `APPROVED` status.

### `ingestion_runs`

Operational processing state is separate from the simple document lifecycle.
For example, a document can remain `DRAFT` while its ingestion run is `FAILED`.

- `id`
- `document_version_id`
- `processing_status`: `QUEUED`, `RUNNING`, `READY`, `NEEDS_REVIEW`, or
  `FAILED`
- `extractor_version`
- `renderer_version`
- `vision_provider`
- `vision_model`
- `vision_prompt_version`
- `chunking_version`
- `embedding_model`
- `chunk_count`
- `source_format`
- `normalized_path`
- `quality_report`
- `error_code`
- `error`
- `started_at`
- `completed_at`

### `chunks`

- `id`
- `document_version_id`
- `parent_section_id`
- `sequence_number`
- `text`
- `source_locator`
- `content_hash`
- `qdrant_point_id`

`source_locator` contains the page, slide, worksheet/range, or transcript
timestamp needed to trace a result to its source.

## Document extraction

All extractors emit the same normalized structure before chunking.

The [visual ingestion pipeline](docs/ingestion-pipeline.md) focuses on the PPTX
native-plus-vision path, deterministic reconciliation, and the exact files
produced for Gate 2 inspection. The table below documents the other format
paths and their boundaries.

| Format | Initial extraction strategy |
| --- | --- |
| PDF | Hybrid page extraction: authoritative native page text plus selective rendered-page vision for image-dominant or vector-complex pages |
| PDF transcript | Native page text plus source-provided video name and overall time range; never infer missing speaker or turn timestamps |
| PPTX | Hybrid slide extraction: native title/body/notes plus a rendered-slide vision pass producing Markdown, tables, visual descriptions, labels, and quality findings |
| XLSX | Worksheet title, header row, data rows, and exact cell ranges |
| DOCX | Headings, paragraphs, and tables in source order |
| TXT/Markdown | Shared UTF-8 paragraph path, with Markdown heading and pipe-table recognition |

### PDF hybrid and transcript extraction profile

PDF routing uses the registered document type rather than asking a model to
classify the document. A `transcript` PDF follows the native path only. It
normalizes source-provided video name, overall start/end time, and duration, and
does not infer missing speaker attribution or per-turn timestamps.

Other PDFs use a local structural check before any model call. A page is selected
for visual enrichment when it is image-dominant with little native text or has a
high volume of vector drawing operations. Selection reasons and the underlying
text/image/vector metrics are retained in the normalized page record. Selected
pages are rendered independently with `pdftoppm`, analyzed through the same
provider-neutral OpenAI/Ollama boundary used by PPTX, and reconciled with native
wording remaining authoritative.

Prompt `pdf-page-v1` forbids invented wording, speaker attribution, or timestamps.
Renderer version, image hash, provider, model, prompt version, response hash, and
per-page duration are retained. A provider failure is `FAILED`; a material
native/visual discrepancy, unclassifiable page, or OCR requirement is
`NEEDS_REVIEW`, preserves partial normalized output, and blocks approval.

### PPTX hybrid extraction profile

PPTX is treated as a presentation business document, not merely as another
container for paragraphs. The ingestion request records a content profile such
as `qbr` or `general_presentation`; this profile can later select prompts and
retrieval evaluation sets without changing the source-file format.

Every PPTX uses the following pipeline:

1. Extract machine-readable title, body text, tables, charts, images, and
   speaker notes as native elements with stable IDs and normalized geometry.
2. Render every slide to a stable PNG while preserving slide order and record
   the image hash and render version.
3. Send each slide image independently to the configured vision-model provider.
4. Require structured region-level output containing faithful text, tables,
   chart/diagram meaning, retrieval labels, 0-1000 bounding boxes, and
   uncertainty or discrepancy findings.
5. Apply one global native-to-visual assignment using text similarity, geometry,
   element type, and reading order, then attach high-confidence grouped text
   regions. Matching visual text is suppressed only from the derived retrieval
   view; additional visual meaning is retained. Both raw evidence streams and
   their scored alignment records remain stored.
6. Give native extraction precedence for exact text, numbers, and labels.
   Conflicting visual text remains in the audit alignment but is quarantined
   from the derived retrieval Markdown.
7. Make a second, independent multimodal call for every slide. It compares the
   rendered slide with the native extraction, first visual result, and
   reconciliation using a strict category schema.
8. Map categories to severity in code: atomic-fact conflicts and invented
   relationships are `CRITICAL`, missing key elements are `HIGH`, partial or
   uncertain evidence is `MEDIUM`, format-only findings are `LOW`, and no
   discrepancy is `NONE`. `MEDIUM` and above require human review.
9. Run the deterministic slide linker across the reconciled deck. It adds
   reciprocal `previous`, `next`, `references`, `referenced_by`, `continues`,
   `continued_from`, `same_section`, and `same_topic` metadata. Every confidence
   stores its contributing order, title, entity, label, and TF-IDF content
   signals; semantic links below their configured thresholds are omitted.
10. Write `PPTX-LLM-SLIDE-REPORT.md` beside the source artifact. For every
   slide it shows the rendered image, native extraction, raw structured LLM
   fields, evidence alignment, logical slide relationships, discrepancies,
   provenance, and derived retrieval output.
11. Write `PPTX-GATE-2-EXCEPTIONS.md` and its PDF containing only slides that
   require human review; automatically passed slides are omitted.
12. Save the native elements, visual elements, alignments, review decision,
   slide relationships,
   provenance, and derived retrieval text in `normalized.json`.

The vision provider is accessed through a provider-neutral protocol. Provider,
model, prompt version, render version, response hash, and per-slide timing are
recorded so the result is reproducible and can be regenerated after a model or
prompt change. Credentials and provider-specific parameters remain in service
configuration rather than extraction code. Set `INGESTION_VISION_PROVIDER` and
`INGESTION_VISION_MODEL` for call 1, and `INGESTION_REVIEW_PROVIDER` and
`INGESTION_REVIEW_MODEL` for call 2. The providers and models can be swapped
independently.

A PPTX run is not `READY` when a slide cannot be rendered, required visual
analysis fails, structured output is invalid, or native and visual extraction
show an unresolved material discrepancy. The native result remains available
for inspection, but the version cannot be approved until the failed slide is
successfully regenerated or explicitly resolved through the review workflow.
The service must never silently fall back to native-only publication.

Slide images are ingestion artifacts, not database blobs. They are stored with
the immutable version artifacts and referenced by path and hash. They are not
placed directly in Qdrant payloads.

Scanned PDFs and image-heavy documents must be reported as requiring OCR or
visual extraction. The service must not silently publish empty or poor-quality
content.

OCR is explicitly deferred. Files that require OCR enter `NEEDS_REVIEW`, retain
any partial normalized output, and remain drafts.

For spreadsheets, Qdrant will support discovery of relevant tables and
definitions. Exact financial calculations and authoritative numeric lookup
remain outside semantic retrieval.

## Chunking proposal

The baseline configuration for evaluation is:

- split on document structure before considering token length;
- target approximately 300-450 tokens per child chunk for the initial dense
  model;
- enforce the selected model's input limit after adding the contextual title
  and section header;
- do not cross section, slide, table, or topic boundaries merely to reach a
  target size;
- use limited overlap only within the same logical section;
- retain a parent section identifier;
- prepend document title and section path to the text used for embeddings;
- preserve the unmodified source text for citations.

PPTX is a deliberate format-specific exception to token-target splitting:

- one slide is one retrieval unit and one Qdrant point;
- slides are never combined, and a slide is not split merely to reach the
  general 300-450-token target;
- the embedding text contains the document title, slide title, faithful slide
  text, Markdown tables, concise visual description, and retrieval labels;
- speaker notes remain traceable source content and are included when they fit
  the selected embedding model's verified input limit;
- a slide that exceeds the model input limit is reported as a quality exception
  for review rather than silently truncated or split.

The final sizes will be selected from retrieval evaluation rather than treated
as permanent constants.

## Qdrant model

The initial design uses one collection exposed through a stable alias:

```text
liveqna_knowledge_current
```

Each point represents one retrieval unit and contains two named vectors:

- `dense_v1`: semantic embedding;
- `lexical_bm25_v1`: sparse BM25 representation.

Both embeddings will be generated locally through Qdrant's FastEmbed library.
The initial models are:

| Role | Initial model | Reason |
| --- | --- | --- |
| Dense semantic retrieval | `BAAI/bge-large-en-v1.5` | Larger English-focused FastEmbed model selected to prioritize retrieval quality while using the available 12 GB GPU |
| Sparse lexical retrieval | `Qdrant/bm25` | Exact terminology, acronyms, service names, document identifiers, and hybrid retrieval |

The FastEmbed and model versions must be pinned in the service dependencies and
recorded for each ingestion run. Changing the dense model requires a new named
vector or collection version and re-embedding the affected corpus.

Dense inference will use `fastembed-gpu` with the CUDA execution provider in
the ingestion container. The vectors are still stored and searched by the
existing Qdrant container; Qdrant itself does not need to perform the embedding
inference.

GPU acceleration improves embedding throughput but does not change the quality
of a particular model. Its value here is that it makes a larger dense model
practical. Offline corpus ingestion must not run during a live call. Later,
live query embedding will use the same dense model and must be benchmarked while
the local Whisper STT backend is active so GPU contention and end-to-end latency
are measured rather than assumed.

### Dense model options for evaluation

The first implementation will use only the selected large baseline. The
following FastEmbed-supported candidates remain available for a controlled
comparison or fallback:

| Candidate | Dimensions | When to evaluate it |
| --- | ---: | --- |
| `mixedbread-ai/mxbai-embed-large-v1` | 1024 | Compare another large English model if the labelled evaluation shows weaknesses in the BGE baseline |
| `BAAI/bge-base-en-v1.5` | 768 | Use as the lower-resource fallback if live query latency or concurrent STT GPU usage is unacceptable |
| `BAAI/bge-small-en-v1.5` | 384 | Use only as the fastest fallback if the larger models do not satisfy the live latency budget |

These are locally executable FastEmbed models, not managed API calls. Selection
will be based on the labelled retrieval evaluation rather than model size
alone.

Required payload:

```text
project_id
document_id
document_version_id
document_type
version_label
status
title
section_path
source_locator
text
content_hash
```

Payload indexes will be created before ingestion for fields used by retrieval
filters, particularly `project_id`, `document_id`, `document_version_id`,
`document_type`, `content_profile`, and `status`.

The first hybrid-search baseline will retrieve dense and sparse candidates and
combine their ranks using Reciprocal Rank Fusion. Model choice and ranking
weights remain evaluation decisions, not assumptions in this plan.

For PPTX, one point represents one slide. Its payload also contains:

```text
content_profile
slide_number
slide_title
slide_labels
slide_image_path
slide_image_hash
visual_extraction_version
```

The initial PPTX implementation embeds the derived, deduplicated retrieval
representation while preserving both raw evidence streams; it
does not add an image vector. An `image_dense_v1` vector may be introduced only
if labelled evaluation demonstrates that native-plus-vision text retrieval
misses relevant visually expressed evidence. This keeps the first retrieval
baseline measurable and avoids adding multimodal query complexity without
evidence of benefit.

## Lifecycle operations

### Ingest

1. Select a project and logical document, or create them.
2. Preserve the uploaded file as a new immutable version.
3. Calculate its content hash and reject an accidental duplicate.
4. Register the version as `DRAFT`.
5. Extract and normalize it. For PPTX, render and visually enrich every slide.
6. Validate extraction quality, then create the format-specific retrieval
   units.
7. Generate dense and sparse vectors.
8. Upsert the retrieval units to Qdrant with `status=DRAFT`.
9. Mark the ingestion run `READY` only after counts and source locators are
   validated.

### Approve

1. Require an explicit version identifier and confirmation from the local
   owner.
2. Require the version's ingestion run to be `READY`.
3. Lock the logical document against concurrent approval.
4. Change the existing `APPROVED` version, if present, to `ARCHIVED`.
5. Change the selected `DRAFT` version to `APPROVED`.
6. Publish the matching status changes to Qdrant.
7. Verify that exactly one approved version is searchable.

The user-visible lifecycle remains three states. Any internal publication or
reconciliation state is operational metadata and must not introduce another
document status.

### Delete draft

A draft may be deleted when it was ingested accidentally or is no longer
needed. Approved or archived versions are retained for traceability.

## Service interface before UI integration

The ingestion service will first expose an internal REST API and equivalent CLI
or test-harness operations. These provide a stable contract that can be tested
without involving browser state or the existing UI service.

Phases 1 and 2 implement this REST contract:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/projects` | Create a project |
| `GET` | `/projects` | List projects |
| `POST` | `/projects/{project_id}/documents` | Create a logical document |
| `GET` | `/projects/{project_id}/documents` | List the project's documents |
| `POST` | `/documents/{document_id}/versions` | Upload an immutable draft version |
| `GET` | `/documents/{document_id}/versions` | List the document's versions |
| `GET` | `/versions/{version_id}` | Get one version |
| `POST` | `/versions/{version_id}/approve` | Approve a draft and archive its predecessor |
| `DELETE` | `/versions/{version_id}` | Delete a draft only |
| `GET` | `/versions/{version_id}/extraction` | Inspect the latest extraction run and quality report |
| `POST` | `/versions/{version_id}/extract` | Retry extraction as a new auditable run |
| `GET` | `/versions/{version_id}/normalized` | Inspect normalized blocks and source locators |

Later phases will add these conceptual retrieval and repair operations:

```text
search-preparation
search-live
search-history
reconcile-index
```

The local LiveQnA UI now includes an Ingestion area providing:

- project selection or creation;
- local file upload;
- source format inferred from the filename extension;
- per-version content profile and user-defined retrieval metadata;
- asynchronous extraction and normalization progress;
- PPTX hybrid extraction through the existing normalization pipeline;
- draft, approved, and archived document lists;
- explicit approval confirmation;
- draft deletion;
- normalized JSON download.

This UI phase stops at normalization. Chunking, embeddings, Qdrant publication,
and test search remain later phases.

The browser will upload a file to the UI backend, which will proxy it to the
ingestion service. The UI consumes the already-tested service contract rather
than introducing separate lifecycle or ingestion logic.

There is no authentication layer in the first implementation. `approved_by`
will record a configured local-owner display name rather than a user account.

## Consistency and recovery

PostgreSQL is authoritative; Qdrant is a rebuildable projection.

The service will use deterministic point identifiers and idempotent jobs. A
small publication/outbox mechanism will record status changes that still need
to be reflected in Qdrant. A reconciliation operation must be able to detect
and repair differences between PostgreSQL and Qdrant.

This prevents a failed Qdrant update from silently exposing an incorrect
version during live retrieval.

## Evaluation

Before connecting ingestion to the live orchestrator:

1. The owner provides representative PDF, PPTX, XLSX, DOCX, and transcript
   files at the extraction review gate.
2. For PPTX, include text-heavy slides, Markdown-convertible tables, charts,
   diagrams, and visually expressed relationships. Inspect native extraction,
   rendered PNG, visual output, combined Markdown, labels, discrepancies, and
   source location side by side.
3. Verify that native text remains exact and that visual enrichment adds useful
   evidence without inventing unsupported claims.
4. Create a small set of realistic questions with known relevant evidence,
   including questions answerable only from a chart or diagram.
5. Measure dense-only, sparse-only, and hybrid retrieval.
6. Compare native-only and native-plus-vision PPTX retrieval as a controlled
   ablation. Add image embeddings only if this evidence shows they are needed.
7. Compare chunking configurations using recall, precision, and ranking
   metrics.
8. Confirm that live search cannot return draft or archived retrieval units.

Answer generation is not part of this evaluation phase. The first objective is
to demonstrate that the correct evidence can be found and traced reliably.

## Acceptance criteria

- Every new version starts as `DRAFT`.
- Approval is always an explicit human action.
- Approving a new version archives the previously approved version.
- Only one version of a logical document is approved at a time.
- Preparation search returns draft and approved content only.
- Live search returns approved content only.
- Archived content is returned only by explicit historical search.
- Re-ingesting the same file does not create duplicate versions or chunks.
- Every result identifies its original page, slide, worksheet/range, or
  transcript timestamp.
- Failed or incomplete extraction cannot be approved.
- Every PPTX slide has one rendered image, one normalized slide record, and one
  retrieval unit with matching document/version/slide provenance.
- PPTX tables are represented as Markdown and charts or diagrams have a concise
  evidence-grounded visual description.
- Native PPTX wording and speaker notes remain distinguishable from generated
  visual descriptions and labels.
- A PPTX visual-processing failure or unresolved material discrepancy cannot
  silently fall back to native-only approval.
- Qdrant can be rebuilt from PostgreSQL and the preserved source files.
- No live-call component is required for offline ingestion to run.

## Phased implementation and test plan

Each phase must produce a directly testable result. Work stops at each review
gate before the next phase begins. The existing LiveQnA UI is not changed until
the underlying ingestion pipeline and hybrid retrieval have been validated.

### Phase 0: service and database foundation

Deliver:

- ingestion service skeleton with health endpoint;
- PostgreSQL container and persistent local volume;
- migration mechanism and initial schema;
- service configuration for PostgreSQL, Qdrant, file storage, and model cache;
- Docker health checks and dependency wiring;
- no document extraction or UI changes.

Test:

- containers start and become healthy;
- PostgreSQL migrations apply to an empty database and can run again safely;
- ingestion service reaches PostgreSQL and Qdrant;
- persistent data survives a normal container restart.

**Review gate 0:** inspect the container boundary, configuration, and schema
before adding business behavior.

Phase 0 verification completed on 2026-10-12:

- PostgreSQL and ingestion containers start and report healthy;
- migration `001_initial_schema` creates the six expected ingestion tables;
- restarting ingestion reports no migrations reapplied;
- restarting PostgreSQL preserves the migration ledger in its named volume;
- the ingestion health endpoint reports both PostgreSQL and Qdrant available;
- five service unit tests pass;
- no extraction, embedding, lifecycle endpoint, Qdrant collection, or UI work
  has been introduced.

Phase 0 passed Review Gate 0 when Phase 1 was explicitly started.

### Phase 1: document registry and lifecycle

Deliver:

- project, document, document-version, and ingestion-run persistence;
- immutable file-version storage;
- `DRAFT -> APPROVED -> ARCHIVED` lifecycle operations;
- automatic archival of the previous approved version;
- internal REST and CLI/test-harness operations;
- no extraction, embeddings, Qdrant points, or UI changes.

Test:

- a newly registered version is always `DRAFT`;
- approving v2 archives approved v1 in the same logical operation;
- two approved versions of one document cannot exist;
- a draft can be deleted, while approved and archived versions are retained;
- duplicate file hashes do not create accidental duplicate versions;
- lifecycle and files remain correct after service restart.

**Review gate 1:** manually exercise the lifecycle and confirm that the three
states remain understandable before processing document contents.

Phase 1 verification completed on 2026-10-12:

- projects contain independent groups of logical documents;
- uploaded versions use immutable UUID-based paths and SHA-256 hashes;
- every new version starts as `DRAFT`;
- approving v2 changes approved v1 to `ARCHIVED` in the same transaction;
- the database constraint prevents multiple approved versions per document;
- duplicate content and duplicate version labels are rejected;
- draft deletion succeeds while approved and archived versions are protected;
- lifecycle records and retained files survive an ingestion-service restart;
- nine ingestion unit tests pass;
- no extraction, embedding, Qdrant point, search, or UI functionality has been
  introduced.

During Phase 1, `READY` meant only that the immutable file was stored and
hash-validated. As of Phase 2, approval requires the latest extraction run to
be `READY` and to reference a saved normalized output.

The repeatable lifecycle harness is:

```bash
docker compose exec -T ingestion python /app/scripts/phase1_smoke.py
```

It creates a small review project intentionally so the resulting archived and
approved versions can be inspected after restart.

Phase 1 passed Review Gate 1 when Phase 2 was explicitly started.

### Phase 2: extraction and normalized document model

Deliver:

- PDF (including the transcript profile), PPTX, XLSX, DOCX, and text/Markdown
  extractors;
- one normalized block model shared by every format;
- page, slide, worksheet/range, and timestamp source locators;
- PPTX per-slide rendering with stable image hashes;
- provider-neutral local/remote vision-model adapter and structured response
  validation;
- PPTX Markdown tables, chart/diagram descriptions, labels, and native/visual
  discrepancy findings;
- recorded renderer, provider, model, prompt, response, and per-slide timing
  provenance;
- extraction quality report and clear unsupported/OCR-required outcomes;
- saved normalized output that can be inspected before chunking;
- no embeddings, Qdrant points, or UI changes.

Test:

- unit fixtures for each format;
- deterministic native normalized output for the same source file;
- tables, headings, slide notes, worksheet headers, text lines, and PDF pages
  retain their source locations;
- PPTX fixtures verify slide order, image hashes, structured vision responses,
  element geometry, duplicate suppression, complementary visual evidence,
  conflict preservation, Markdown tables, labels, and derived retrieval text;
- provider failures, invalid structured responses, render failures, and
  material discrepancies prevent `READY` status;
- repeated normalization from the same preserved native and vision outputs is
  deterministic, while model responses are retained and hashed rather than
  assumed to be deterministic;
- empty, corrupt, and encrypted files fail clearly; OCR-required files enter
  `NEEDS_REVIEW`; all remain drafts;
- extracted text can always be traced to the original document.

**Review gate 2:** the owner supplies representative files and inspects the
normalized extraction output. For PPTX, the review compares each original
slide, rendered PNG, native content, visual result, combined Markdown, labels,
and discrepancy report. Chunking does not begin until this output and the
configured vision-provider boundary are accepted.

Phase 2 baseline verification completed on 2026-10-12:

- migration `002_extraction_results` records source format, normalized-output
  path, quality report, and explicit error code;
- PDF, PPTX, XLSX, DOCX, Markdown, and text extractors share one normalized
  block model;
- normalized JSON is stored beside each immutable source version;
- locators retain page, slide, worksheet/cell range, paragraph/table, or text
  line information as applicable;
- repeated extraction of the same source produces identical normalized output;
- empty, corrupt, unsupported, and encrypted inputs fail with explicit codes;
  OCR-required inputs enter `NEEDS_REVIEW`; their document versions remain
  drafts;
- approval succeeds only after successful extraction;
- the Phase 2 API harness passes for all supported formats, and the Phase 1
  lifecycle harness still passes;
- 23 ingestion unit tests pass;
- no chunking, embedding, Qdrant point, search, or UI functionality has been
  introduced.

The repeatable extraction harness is:

```bash
docker compose exec -T ingestion python /app/scripts/phase2_smoke.py
```

Current state: the native baseline is implemented, but Phase 2 remains open.
The PPTX renderer, OpenAI/Ollama provider-neutral vision boundary, hybrid
normalized slide model, provenance, partial-output preservation, and
approval-blocking quality checks are implemented. Migration
`003_pptx_hybrid_extraction` records content profile and run-level renderer and
vision provenance.

Phase 2 hybrid verification completed on 2026-10-12:

- LibreOffice renders one stable PNG per slide and the integration test verifies
  repeatable image hashes;
- strict provider-neutral output captures Markdown transcription, Markdown
  tables, visual description, labels, discrepancies, and uncertainties;
- native PPTX wording and speaker notes remain separate and authoritative;
- provider, model, prompt, response hash, image hash, render version, and
  per-slide duration are recorded;
- OpenAI Responses API and local Ollama adapters pass contract tests;
- 28 ingestion unit tests pass and the separate LibreOffice integration test
  passes;
- real slides 5, 6, 14, 22, and 24 from `2026 PoC Template.pptx` completed with
  no request or schema failures using `gpt-4.1-mini`;
- visual inspection confirmed faithful rendering and useful table/diagram
  extraction, but the model guessed one component name hidden by the `Example`
  overlay on slide 24 instead of reporting uncertainty;
- prompt `pptx-slide-v2` explicitly forbade reconstructing covered or
  partially visible text;
- a one-slide confirmation on 2026-10-14 using `gpt-4.1-mini` and prompt v2
  completed without request or schema errors. It omitted the hidden component
  name and explicitly reported the text behind the `Example` box as obscured.

Prompt `pptx-slide-v4` performs an independent region-level extraction from the
rendered slide without receiving native PPTX wording. Reconciler
`element-fusion-v3` then applies one global assignment across native and visual
elements using text similarity, geometry, element type, and reading order. It
supports grouped text regions, suppresses confirmed duplicates, retains
unmatched visual meaning, and raises deterministic numeric conflicts in addition
to model-reported conflicts. Native exact wording has precedence and conflicting
visual wording is quarantined from retrieval Markdown. Raw native and visual
evidence remains unchanged and the scored alignments are stored in the
normalized output. Schema 1.5 adds the independent review decision and its
provider/model/prompt/hash/timing provenance to every normalized slide.

The 2026-10-15 five-slide Gate 2 run uses prompt v4 directly: all selected
slides returned region-level elements with bounding boxes and were reconciled by
element-fusion-v3. A second review call was then made for each slide. Slides 5,
6, 14, and 24 automatically passed; slide 22 was queued for human review because
the `Example` overlay obscures the central CloudWatch relationship. Older
prompt-v2 responses remain readable through the legacy
compatibility path, but they do not validate geometry-aware matching.

The repeatable five-slide review command is:

```bash
PYTHONPATH=services/ingestion uv run python \
  services/ingestion/scripts/phase2_pptx_review.py \
  "/path/to/presentation.pptx" \
  /tmp/liveqna-pptx-review \
  --slides 5,6,14,22,24 \
  --content-profile general_presentation
```

Review Gate 2 remains open until the owner inspects the generated report and
rendered slides. The prompt-v2 slide-24 confirmation is stored separately under
`pptx-v2-slide24-confirmation` so the original five-slide review remains intact.

The remaining-format review completed on 2026-10-14 with these results:

- the representative DOCX passed: all non-empty body paragraphs and tables are
  represented, while its embedded images are non-retrieval-bearing Aegon logos;
- the representative XLSX passed: worksheet, header, row, and cell-range
  provenance are retained (large row-aware splitting remains Phase 3 work);
- selective PDF detection now identifies architecture pages 2, 4, 6, 8, and 10
  plus vector-heavy financial pages 17 and 21; all seven pages render locally
  with stable image hashes;
- the representative transcript PDF retains all 15 pages and 50,062 characters;
  its video name and overall `00:00:00`–`00:59:48` range remain in the native
  text, while the source contains no reliable per-turn speakers or timestamps;
- the transcript metadata is normalized without inferred speaker or turn data;
- 48 ingestion tests pass, with one optional LibreOffice test skipped.

Review Gate 2 remains open for PPTX slide 22 and for visual-quality inspection
of the seven selected private PDF pages. External OpenAI review was not run for
those PDF pages because explicit approval is required to send them outside the
environment.

The stable review entry point is `.cache/ingestion/runs/latest.json`. It points
to the newest immutable inspection batch under
`.cache/ingestion/runs/<YYYY-MM-DD_HH-MM-SS+offset>/`; creating another batch
does not overwrite earlier inspection evidence. `.cache/ingestion` is reserved
for these inspection runs; persistent uploaded versions live separately under
`.data/ingestion/documents`. Each run contains exactly one
source-matched `normalized.json` per sample, organized under a document-type
folder and then a source-document folder. The PPTX folder separately provides
`native-elements.json`, `visual-elements.json`, `reconciliation.json`,
`review-decisions.json`, `normalized.json`, the full slide report, the PPTX
exception report/PDF, and rendered-slide images. PDF folders
keep their normalized output beside any rendered-page evidence; transcript,
DOCX, XLSX, and Markdown/text samples each have their own folder. The owner
records `ACCEPT` or `REJECT` in the checklist's Human decision column; only rows
marked `HUMAN REVIEW REQUIRED` keep the gate open. `INGESTION_REVIEW_TIMEZONE` controls
the human-readable run timestamp and defaults to `Europe/Rome`. The package can
be regenerated locally, without new model or network calls, using
`scripts/build_gate2_review_package.py`.

For the representative PPTX review, both the native blocks and visual slide
records are restricted to the same slides: 5, 6, 14, 22, and 24. Production
PPTX ingestion continues to process the complete presentation.

### Phase 3: structure-aware chunking

Deliver:

- format-aware block grouping and splitting;
- 300-450-token baseline target with the model input limit enforced;
- contextual document title and section path for embedding input;
- parent-section identifiers and limited within-section overlap;
- one-slide/one-retrieval-unit PPTX handling with combined native and visual
  content;
- stable deterministic chunk IDs and chunk manifests;
- no embeddings, Qdrant points, or UI changes.

Test:

- chunks do not cross unrelated sections, slides, tables, or transcript topics;
- PPTX slides are neither merged nor split, and each slide produces one stable
  retrieval-unit identifier;
- an over-limit PPTX slide is reported for review instead of being silently
  truncated or split;
- contextual headers do not remove or alter source text;
- no embedding input exceeds the selected model limit;
- chunk IDs remain stable when the same version is processed again;
- every chunk retains a valid source locator.

**Review gate 3:** inspect representative chunks alongside their original
pages, slides, sheets, and transcript positions.

### Phase 4: local embeddings and Qdrant publication

Deliver:

- local GPU dense inference with `BAAI/bge-large-en-v1.5`;
- local sparse representation with `Qdrant/bm25`;
- named dense and sparse Qdrant vectors;
- payload schema and filter indexes, including PPTX slide provenance and
  content profile;
- deterministic, idempotent point publication;
- model and embedding-version recording;
- still no UI changes.

Test:

- CPU/GPU provider and available VRAM are reported at startup;
- a known chunk produces the expected vector dimensions;
- all PostgreSQL chunks have exactly one matching Qdrant point;
- every eligible PPTX slide has exactly one Qdrant point and no image bytes are
  stored in its payload;
- repeated ingestion does not duplicate points;
- payload filters isolate project, status, document, and version;
- drafts cannot appear in an approved-only Qdrant query.

**Review gate 4:** verify point counts, payloads, filters, model metadata, and
GPU behavior before implementing hybrid search.

### Phase 5: hybrid search and retrieval evaluation

Deliver:

- dense-only, sparse-only, and hybrid query modes;
- Reciprocal Rank Fusion baseline;
- preparation, live, and historical status policies;
- traceable results containing source locators;
- labelled evaluation format and metric report;
- no answer generation and no UI changes.

Test:

- preparation search returns only `DRAFT` and `APPROVED`;
- live search returns only `APPROVED`;
- historical search returns archived versions only when explicitly requested;
- exact AWS/Azure service names and identifiers exercise BM25 recall;
- paraphrased architectural questions exercise semantic recall;
- chart-, table-, and diagram-grounded PPTX questions exercise the combined
  slide representation;
- dense, sparse, and hybrid precision/recall and ranking metrics are compared;
- native-only and native-plus-vision PPTX retrieval are compared and retrieval
  misses are classified before considering an image vector;
- BGE Large is compared with a lower-resource fallback if quality or latency
  warrants it.

**Review gate 5:** review actual results for the labelled questions, decide
from evidence whether an image vector is necessary, and approve the retrieval
baseline before reliability or UI work.

### Phase 6: recovery and operational reliability

Deliver:

- PostgreSQL publication outbox;
- retryable ingestion jobs;
- PostgreSQL-to-Qdrant reconciliation command;
- complete Qdrant rebuild from PostgreSQL and preserved files;
- structured phase timings and error reporting;
- retryable and resumable per-slide visual enrichment without reprocessing
  already validated slides;
- still no UI changes.

Test:

- simulate Qdrant being unavailable during publication;
- retry without duplicating points;
- detect and repair missing, stale, and incorrectly labelled points;
- rebuild an empty Qdrant collection and compare counts and hashes;
- interrupt and resume extraction, embedding, and publication jobs safely.
- retry a failed PPTX slide without duplicating slide artifacts or Qdrant
  points.

**Review gate 6:** approve failure recovery and rebuild evidence before the
service becomes accessible through the application UI.

### Phase 7: local LiveQnA UI integration

Deliver:

- Knowledge/Ingestion tab in the existing UI;
- local file upload through the UI proxy;
- project, type, title, and version inputs;
- processing progress and extraction errors;
- PPTX content profile, slide previews, labels, and native/visual quality
  findings;
- draft, approved, and archived document views;
- explicit approval and draft-deletion controls;
- preparation, live, and historical test-search views;
- no live orchestrator retrieval yet.

Test:

- end-to-end browser upload through extraction and Qdrant publication;
- approve a second version and observe the first becoming archived;
- UI state remains consistent after page reload and service restart;
- browser operations cannot bypass service lifecycle rules;
- a PPTX with an unresolved visual-processing failure cannot be approved;
- retrieval results display their document and source location.

**Review gate 7:** manually inspect and approve the complete local ingestion UI
before connecting it to live-call orchestration.

### Phase 8: live retrieval integration

This is a separate, final integration phase after the ingestion service is
accepted.

Deliver:

- approved-only retrieval node for the LiveQnA orchestrator;
- local query embedding using the same dense model as the indexed documents;
- retrieval timing and returned-evidence schema;
- explicit no-match/defer behavior;
- no slide rendering, vision-model parsing, or document embedding on the live
  retrieval path;
- no removal of the existing human validation gate.

Test:

- query embedding and hybrid retrieval while local Whisper STT is active;
- GPU memory and latency under the actual concurrent workload;
- live retrieval never returns draft or archived evidence;
- machine-processing latency is measured independently of human review time;
- insufficient approved evidence produces a defer result rather than a guess.

**Review gate 8:** approve live latency, retrieval accuracy, and evidence
boundaries before answer generation is designed or implemented.



