# liveqna

Live audio is transcribed into a browser transcript. A user can capture a
specific section, send it to a LangGraph workflow, and review retrieval-ready
questions generated from that text.

## Question workflow

```text
captured text -> supervisor -> question generator -> human review
                                      ^                 |
                                      +---- regenerate -+

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

Run the orchestrator tests without starting services:

```bash
UV_CACHE_DIR=/tmp/uv-cache PYTHONPATH=services/orchestrator \
  uv run pytest -q services/orchestrator/tests
```

## Offline ingestion

The offline ingestion service is being delivered in independently reviewed
phases. Phase 0 adds its PostgreSQL-backed service foundation and dependency
health checks; it does not yet ingest documents or change the UI.

The reviewed lifecycle and phased implementation plan are documented in
[`services/ingestion/README.md`](services/ingestion/README.md).
