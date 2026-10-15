# liveqna

Live audio is transcribed into a browser transcript. A user can capture a
specific section, send it to a LangGraph workflow, and review retrieval-ready
questions generated from that text.

## Question workflow

```text
select project -> start live call -> captured text -> supervisor
                                                     |
                                                     v
                               human review <- question generator
                                  |    ^
                                  +----+ regenerate

human review -> validate -> retrieval placeholder
             -> reject   -> end
```

The orchestrator is a separate Docker service with SQLite-backed LangGraph
checkpoints. The UI supports editing, adding, removing, validating, rejecting,
and regenerating questions. Retrieval is intentionally not implemented yet;
validated questions stop at a stable placeholder node.

Both agents can independently use `openai` or `ollama`. The default OpenAI
profile uses `gpt-6-luna` with no reasoning and the fast service tier for the
supervisor, and `gpt-6.1-sol` with low reasoning and the fast tier for
development question generation. Switch the question agent to `ultrafast` for
production latency testing. Provider, model, reasoning effort, and service tier can
be changed independently in the UI. Copy `.env.example` to `.env`, provide
`OPENAI_API_KEY` for OpenAI, or make Ollama available at `OLLAMA_BASE_URL`.

## Provider adapters

Agent model configuration is driven by adapters in
`services/orchestrator/app/providers/`. Each adapter satisfies the
`ProviderAdapter` protocol and owns its typed parameter validation, model
capabilities, LangChain client construction, structured-output behavior, and UI
parameter metadata. The provider registry publishes that metadata through
`/model-options`; the browser renders the controls dynamically.

To add a provider, implement the protocol and register the adapter in
`providers/registry.py`. Provider-specific conditions belong in the adapter,
not in the UI or graph. Model clients are cached by provider, model, and the
normalized parameter set, so catalog rendering adds no inference-path network
call.

```bash
docker compose up --build
```

## GPU runtime switching

The local Live Q&A transcription model is preloaded by default. Runtime
ownership changes only from the two start actions:

- **Start ingestion:** when ingestion is configured with local Ollama models,
  capture is stopped and the local Live Q&A models are unloaded before the
  upload jobs begin. With remote-only ingestion, both workflows remain
  available in parallel and no Live Q&A model is unloaded.
- **Start Live Q&A:** any idle local ingestion models are unloaded, then the
  configured transcription model is loaded if it is not already resident.
  Starting Live Q&A is rejected while a local-model ingestion job is active.
- **Stop Live Q&A:** stops audio capture only. It deliberately leaves the Live
  Q&A model loaded for a low-latency restart.

Remote OpenAI models do not use local GPU memory and therefore do not require
runtime unloading.

Run the orchestrator tests without starting services:

```bash
UV_CACHE_DIR=/tmp/uv-cache PYTHONPATH=services/orchestrator \
  uv run pytest -q services/orchestrator/tests
```

## Offline ingestion

The offline ingestion service is being delivered in independently reviewed
phases. Phases 0 and 1 provide its PostgreSQL-backed foundation plus the
project, document-version, and approval lifecycle. The Phase 2 extraction
baseline supports PDF, PPTX, XLSX, DOCX, Markdown, and text and is awaiting
representative-file review. The UI now includes an **Ingestion** tab for
multi-file asynchronous upload, per-version content profiles and user-defined
retrieval metadata, normalization progress, normalized JSON download, and
explicit approval or rejection. A presentation requiring review shows the full
host path of its stored source and offers **Accept**, **Reingest**, and
**Reject**. Reingest rereads that same editable draft file, recalculates its
content hash, and replaces the draft's normalized output asynchronously. This
phase stops at normalization: chunking, embeddings, and Qdrant publication are
not implemented yet.

The ingestion library groups every version under its document. The document
**×** action requires confirmation and queues a complete background purge. It
waits for any running extraction, removes future Qdrant points and all stored
artifacts, then deletes the document, versions, runs, and chunk records. Failed
purges remain visible and can be retried; completed deletions retain only a
small audit tombstone.

The reviewed lifecycle and phased implementation plan are documented in
[`services/ingestion/README.md`](services/ingestion/README.md).
