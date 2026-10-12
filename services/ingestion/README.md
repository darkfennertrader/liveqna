# LiveQnA ingestion service

> Status: Phase 0 implemented and awaiting Review Gate 0. Phases 1 and later
> have not started.

## Purpose

The ingestion service prepares knowledge before a live call. It accepts project
documents, extracts and chunks their content, creates lexical and semantic
representations, and publishes searchable chunks to Qdrant.

The completed service will accept documents through the local LiveQnA
application UI. During development, the pipeline will first be exercised
through its internal API, CLI, and automated tests. UI integration happens only
after the independent ingestion and retrieval phases pass their review gates.
File content and embeddings remain local; the first implementation will not
send document text to a managed embedding API.

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
- extract text and structural information from supported formats;
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
- `processing_status`: `QUEUED`, `RUNNING`, `READY`, or `FAILED`
- `extractor_version`
- `chunking_version`
- `embedding_model`
- `chunk_count`
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

All extractors will emit the same normalized structure before chunking.

| Format | Initial extraction strategy |
| --- | --- |
| PDF | Headings, paragraphs, tables, and page numbers |
| PPTX | Slide title, body, notes, and slide number |
| XLSX | Worksheet, logical table, headers, and row groups |
| Transcript | Speaker turns, timestamps, and topic boundaries when available |
| DOCX/Markdown/text | Headings, paragraphs, and tables |

Scanned PDFs and image-heavy documents must be reported as requiring OCR or
visual extraction. The service must not silently publish empty or poor-quality
content.

OCR is explicitly deferred from the first implementation. Files that require
OCR will fail extraction with a clear unsupported-document result and remain
drafts.

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

The final sizes will be selected from retrieval evaluation rather than treated
as permanent constants.

## Qdrant model

The initial design uses one collection exposed through a stable alias:

```text
liveqna_knowledge_current
```

Each point represents one retrieval chunk and contains two named vectors:

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
`document_type`, and `status`.

The first hybrid-search baseline will retrieve dense and sparse candidates and
combine their ranks using Reciprocal Rank Fusion. Model choice and ranking
weights remain evaluation decisions, not assumptions in this plan.

## Lifecycle operations

### Ingest

1. Select a project and logical document, or create them.
2. Preserve the uploaded file as a new immutable version.
3. Calculate its content hash and reject an accidental duplicate.
4. Register the version as `DRAFT`.
5. Extract, normalize, and chunk it.
6. Generate dense and sparse vectors.
7. Upsert the chunks to Qdrant with `status=DRAFT`.
8. Mark the ingestion run `READY` only after counts and source locators are
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

Conceptual operations:

```text
create-project
ingest-document
list-documents
show-document-versions
approve-version
delete-draft
search-preparation
search-live
search-history
reconcile-index
```

After the service pipeline passes its review gates, a Knowledge/Ingestion area
will be added to the local LiveQnA UI. It will provide:

- project selection or creation;
- local file upload;
- document title, type, and version fields;
- extraction and embedding progress;
- draft, approved, and archived document lists;
- explicit approval confirmation;
- draft deletion;
- preparation, live, and historical test search.

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
2. Inspect normalized extraction output and source locations.
3. Create a small set of realistic questions with known relevant evidence.
4. Measure dense-only, sparse-only, and hybrid retrieval.
5. Compare chunking configurations using recall, precision, and ranking
   metrics.
6. Confirm that live search cannot return draft or archived chunks.

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

Current state: stopped at Review Gate 0. Phase 1 requires explicit approval.

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

### Phase 2: extraction and normalized document model

Deliver:

- PDF, PPTX, XLSX, DOCX, text/Markdown, and transcript extractors;
- one normalized block model shared by every format;
- page, slide, worksheet/range, and timestamp source locators;
- extraction quality report and clear unsupported/OCR-required outcomes;
- saved normalized output that can be inspected before chunking;
- no embeddings, Qdrant points, or UI changes.

Test:

- unit fixtures for each format;
- deterministic normalized output for the same source file;
- tables, headings, slide notes, worksheet headers, and transcript timestamps
  retain their source locations;
- empty, corrupt, encrypted, and OCR-required files fail clearly and remain
  drafts;
- extracted text can always be traced to the original document.

**Review gate 2:** the owner supplies representative files and inspects the
normalized extraction output. Chunking does not begin until this output is
accepted.

### Phase 3: structure-aware chunking

Deliver:

- format-aware block grouping and splitting;
- 300-450-token baseline target with the model input limit enforced;
- contextual document title and section path for embedding input;
- parent-section identifiers and limited within-section overlap;
- stable deterministic chunk IDs and chunk manifests;
- no embeddings, Qdrant points, or UI changes.

Test:

- chunks do not cross unrelated sections, slides, tables, or transcript topics;
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
- payload schema and filter indexes;
- deterministic, idempotent point publication;
- model and embedding-version recording;
- still no UI changes.

Test:

- CPU/GPU provider and available VRAM are reported at startup;
- a known chunk produces the expected vector dimensions;
- all PostgreSQL chunks have exactly one matching Qdrant point;
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
- dense, sparse, and hybrid precision/recall and ranking metrics are compared;
- BGE Large is compared with a lower-resource fallback if quality or latency
  warrants it.

**Review gate 5:** review actual results for the labelled questions and approve
the retrieval baseline before reliability or UI work.

### Phase 6: recovery and operational reliability

Deliver:

- PostgreSQL publication outbox;
- retryable ingestion jobs;
- PostgreSQL-to-Qdrant reconciliation command;
- complete Qdrant rebuild from PostgreSQL and preserved files;
- structured phase timings and error reporting;
- still no UI changes.

Test:

- simulate Qdrant being unavailable during publication;
- retry without duplicating points;
- detect and repair missing, stale, and incorrectly labelled points;
- rebuild an empty Qdrant collection and compare counts and hashes;
- interrupt and resume extraction, embedding, and publication jobs safely.

**Review gate 6:** approve failure recovery and rebuild evidence before the
service becomes accessible through the application UI.

### Phase 7: local LiveQnA UI integration

Deliver:

- Knowledge/Ingestion tab in the existing UI;
- local file upload through the UI proxy;
- project, type, title, and version inputs;
- processing progress and extraction errors;
- draft, approved, and archived document views;
- explicit approval and draft-deletion controls;
- preparation, live, and historical test-search views;
- no live orchestrator retrieval yet.

Test:

- end-to-end browser upload through extraction and Qdrant publication;
- approve a second version and observe the first becoming archived;
- UI state remains consistent after page reload and service restart;
- browser operations cannot bypass service lifecycle rules;
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
- no removal of the existing human validation gate.

Test:

- query embedding and hybrid retrieval while local Whisper STT is active;
- GPU memory and latency under the actual concurrent workload;
- live retrieval never returns draft or archived evidence;
- machine-processing latency is measured independently of human review time;
- insufficient approved evidence produces a defer result rather than a guess.

**Review gate 8:** approve live latency, retrieval accuracy, and evidence
boundaries before answer generation is designed or implemented.



