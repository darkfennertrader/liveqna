from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_ingestion_indicator_precedes_live_transcript() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert html.index('id="ingestion-dot"') < html.index('id="conn-dot"')
    assert 'id="ingestion-dot" class="dot green"' in html


def test_automatic_capture_indicator_is_removed() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "Automatic capture" not in html
    assert "auto-capture-dot" not in html
    assert "updateAutomaticCaptureDot" not in javascript


def test_clear_all_warning_names_deleted_and_preserved_data() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "Clear all will permanently delete:" in javascript
    assert "every Gate 2 inspection run in .cache/ingestion/runs" in javascript
    assert "all generated-question runs and their human review state" in javascript
    assert "It will NOT delete projects, ingested documents or versions" in javascript
    assert 'requestJson("/api/clear-all", { method: "DELETE" })' in javascript


def test_ingestion_indicator_pulses_only_while_work_is_active() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'classList.toggle("working", ingestionRunning && !liveBusy)' in javascript
    assert "setIngestionWorking(ingestionActive)" in javascript


def test_live_and_ingestion_indicators_show_mutual_exclusion() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    backend = (STATIC.parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert 'ingestionDotEl.classList.toggle("red", liveBusy)' in javascript
    assert 'connDot.classList.toggle("red", ingestionRunning)' in javascript
    assert 'connDot.classList.toggle("working", liveBusy && !ingestionRunning)' in javascript
    assert "end Live Q&A before starting document ingestion" in backend
    assert "wait for document ingestion to finish before starting Live Q&A" in backend


def test_questions_hide_generation_source_references() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'source.className = "question-source"' not in javascript
    assert "Source: “" not in javascript


def test_history_ignore_is_not_highlighted_and_logs_download_as_pdf() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert ".badge.ignore { background: transparent" in html
    assert ".history-item.ignore, .history-item.ignore:hover" in html
    assert '.filter((item) => item.status !== "ignore")' in javascript
    assert 'transitionBadge.textContent = "Previously WAIT"' in javascript
    assert '.badge.previously-wait' in html
    assert 'failureBadge.textContent = "Question generation failed"' in javascript
    assert '.badge.generation-failed' in html
    assert 'new Set(["ignored", "failed", "rejected"])' in javascript
    assert 'return reviewStatus !== "rejected"' in javascript
    assert 'return reviewStatus === "validated"' in javascript
    assert "const hiddenRejected = run.questions.filter" in javascript
    assert "Download logs (PDF)" in html
    assert 'fetch("/api/live-logs/pdf")' in javascript
    assert "Content-Disposition" in javascript
    assert "every persistent Live Q&A log in .data/live-session-logs" in javascript


def test_review_source_path_uses_home_shorthand() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "`Source: ${sourcePath.replace(" in javascript
    assert '/^\\/home\\/ray(?=\\/|$)/, "~"' in javascript


def test_ingestion_is_the_default_tab() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'class="tab-btn active" id="tab-ingestion-btn"' in html
    assert 'class="tab-panel active" id="tab-ingestion"' in html
    assert 'class="tab-btn" id="tab-live-btn"' in html
    assert 'class="tab-panel" id="tab-live"' in html


def test_document_delete_replaces_header_x_and_version_reject() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "ingestion-document-delete" not in html
    assert 'remove.textContent = "×"' not in javascript
    assert 'remove.textContent = "Delete"' in javascript
    assert "Delete the entire document, all versions" in javascript
    assert "rejectIngestionVersion" not in javascript
    assert "action-reject" not in html
    assert ".ingestion-actions .action-delete { margin-left: auto; }" in html
    assert "showDocumentDelete" in javascript
    assert "if (!deleting && !active && showDocumentDelete)" in javascript
    assert '"including its only registered version"' in javascript
    assert "previous versions (${versionCount} versions total)" in javascript
