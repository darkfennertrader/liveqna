import json
from pathlib import Path

from app.live_logs import LiveSessionLogStore


def test_wait_is_deduplicated_when_candidate_becomes_ready(tmp_path: Path) -> None:
    root = tmp_path / "live-session-logs"
    store = LiveSessionLogStore(root, timezone_name="Europe/Rome")
    store.start_session(
        "session-1", project_id="project-1", project_name="Client A"
    )
    store.record_event(
        {
            "kind": "session_candidate",
            "session_id": "session-1",
            "sequence": 1,
            "source_text": "Can you explain",
            "decision": "wait",
            "reason": "More context is required.",
            "confidence": 0.8,
            "assessment_ms": 10,
        }
    )
    store.record_event(
        {
            "kind": "session_candidate",
            "session_id": "session-1",
            "sequence": 2,
            "source_text": "Can you explain the cost increase?",
            "decision": "ready",
            "reason": "The question is complete.",
            "confidence": 0.95,
            "assessment_ms": 12,
            "run_id": "run-1",
        }
    )
    store.attach_questions(
        {
            "run_id": "run-1",
            "status": "awaiting_review",
            "question_generation_ms": 35,
            "questions": [{"question": "Why did the cost increase?"}],
        }
    )

    json_path = next(root.glob("*.json"))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["decision"] == "ready"
    assert len(payload["entries"][0]["superseded_waits"]) == 1
    assert payload["entries"][0]["questions"] == [
        "Why did the cost increase?"
    ]
    assert payload["entries"][0]["datetime_local"]
    assert json_path.with_suffix(".pdf").read_bytes().startswith(b"%PDF")
    assert store.combined_pdf().read_bytes().startswith(b"%PDF")


def test_clear_removes_only_files_inside_log_root(tmp_path: Path) -> None:
    root = tmp_path / "live-session-logs"
    store = LiveSessionLogStore(root)
    store.start_session(
        "session-1", project_id="project-1", project_name="Client A"
    )
    outside = tmp_path / "keep.txt"
    outside.write_text("keep", encoding="utf-8")

    removed = store.clear()

    assert removed == 2
    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert outside.read_text(encoding="utf-8") == "keep"


def test_ready_candidate_records_question_generation_failure(tmp_path: Path) -> None:
    root = tmp_path / "live-session-logs"
    store = LiveSessionLogStore(root, timezone_name="Europe/Rome")
    store.start_session(
        "session-failed", project_id="project-1", project_name="Client A"
    )
    store.record_event(
        {
            "kind": "session_candidate",
            "session_id": "session-failed",
            "sequence": 1,
            "source_text": "Why did revenue change?",
            "decision": "ready",
            "run_id": "run-failed",
        }
    )
    store.attach_questions(
        {
            "run_id": "run-failed",
            "status": "failed",
            "error": "Question generator returned no questions for READY text.",
            "questions": [],
        }
    )

    payload = json.loads(next(root.glob("*.json")).read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["decision"] == "ready"
    assert entry["question_run_status"] == "failed"
    assert entry["questions"] == []
    assert store.combined_pdf().read_bytes().startswith(b"%PDF")


def test_download_filename_contains_human_readable_local_datetime(
    tmp_path: Path,
) -> None:
    store = LiveSessionLogStore(
        tmp_path / "live-session-logs", timezone_name="Europe/Rome"
    )

    filename = store.download_filename()

    assert filename.startswith("LIVE-QA-LOGS_20")
    assert filename.endswith(("_CEST.pdf", "_CET.pdf"))
