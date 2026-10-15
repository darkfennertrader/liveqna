from __future__ import annotations

import shutil
from pathlib import Path


def clear_review_runs_cache(directory: Path) -> int:
    """Remove every Gate 2 inspection entry while preserving its root."""
    directory = directory.resolve()
    if directory.name != "runs":
        raise ValueError("review runs cache directory must end with 'runs'")

    directory.mkdir(parents=True, exist_ok=True)
    removed = 0
    for entry in directory.iterdir():
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    return removed
