# Local storage layout

LiveQnA separates persistent application records from rebuildable working data.

## `.data` — persistent records

- `.data/ingestion/documents/` contains uploaded document versions and their
  normalized/review artifacts. PostgreSQL remains the ingestion registry.
- `.data/live-session-logs/` contains one human-readable PDF and one detailed
  JSON record per Live Q&A session. `LIVE-QA-LOGS.pdf` is the combined download
  produced by the **Download logs (PDF)** button.

These files are application records. Do not delete them as routine cache
maintenance. **Clear all** names and deletes the Live Q&A logs explicitly, but
does not delete ingested documents.

## `.cache` — rebuildable or disposable runtime data

- `.cache/huggingface/` and `.cache/models/` contain downloaded model files.
- `.cache/qdrant/` contains the derived vector index.
- `.cache/orchestrator/` contains LangGraph runtime/checkpoint state.
- `.cache/ingestion/runs/` contains disposable Gate 2 inspection packages.

The model caches can be downloaded again; Qdrant can be rebuilt from approved
normalized documents; orchestration state and Gate 2 inspection runs are
runtime/review working data. **Clear all** removes only
`.cache/ingestion/runs/` from this group.
