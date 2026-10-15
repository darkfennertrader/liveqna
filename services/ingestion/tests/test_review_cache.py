from pathlib import Path

import pytest

from app.review_cache import clear_review_runs_cache


def test_clear_review_runs_cache_removes_contents_but_keeps_root(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "ingestion" / "runs"
    run = runs / "2026-10-15_10-00-00+0200"
    run.mkdir(parents=True)
    (run / "GATE-2-REVIEW.pdf").write_bytes(b"report")
    (runs / "latest.json").write_text("{}", encoding="utf-8")

    removed = clear_review_runs_cache(runs)

    assert removed == 2
    assert runs.is_dir()
    assert list(runs.iterdir()) == []


def test_clear_review_runs_cache_rejects_broad_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must end with 'runs'"):
        clear_review_runs_cache(tmp_path / "ingestion")
