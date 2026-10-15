from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .errors import ExtractionFailure


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    path: Path
    relative_path: str
    content_hash: str


@runtime_checkable
class PdfRenderer(Protocol):
    version: str

    def render(
        self,
        source: Path,
        expected_pages: int,
        page_numbers: list[int],
    ) -> list[RenderedPage]: ...


class PdftoppmPdfRenderer:
    version = "pdftoppm-png-v1"

    def __init__(
        self,
        *,
        pdftoppm_binary: str = "pdftoppm",
        dpi: int = 144,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._pdftoppm_binary = pdftoppm_binary
        self._dpi = dpi
        self._timeout_seconds = timeout_seconds

    def render(
        self,
        source: Path,
        expected_pages: int,
        page_numbers: list[int],
    ) -> list[RenderedPage]:
        selected = sorted(set(page_numbers))
        if not selected:
            return []
        if selected[0] < 1 or selected[-1] > expected_pages:
            raise ExtractionFailure(
                "PDF_RENDER_FAILED",
                "selected PDF page is outside the document page range",
            )

        source = source.resolve()
        output_directory = source.parent / "pages"
        output_directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=".pdf-render-", dir=source.parent
            ) as temporary_name:
                temporary = Path(temporary_name)
                rendered: list[RenderedPage] = []
                for page_number in selected:
                    prefix = temporary / f"page-{page_number:04d}"
                    completed = subprocess.run(
                        [
                            self._pdftoppm_binary,
                            "-f",
                            str(page_number),
                            "-l",
                            str(page_number),
                            "-singlefile",
                            "-png",
                            "-r",
                            str(self._dpi),
                            str(source),
                            str(prefix),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=self._timeout_seconds,
                    )
                    if completed.returncode != 0:
                        detail = (completed.stderr or completed.stdout).strip()
                        if len(detail) > 500:
                            detail = detail[-500:]
                        raise ExtractionFailure(
                            "PDF_RENDER_FAILED",
                            f"page {page_number} rendering failed"
                            + (f": {detail}" if detail else ""),
                        )
                    temporary_image = prefix.with_suffix(".png")
                    if not temporary_image.is_file():
                        raise ExtractionFailure(
                            "PDF_RENDER_FAILED",
                            f"page {page_number} renderer produced no PNG",
                        )
                    destination = (
                        output_directory / f"page-{page_number:04d}.png"
                    )
                    os.replace(temporary_image, destination)
                    rendered.append(
                        RenderedPage(
                            page_number=page_number,
                            path=destination,
                            relative_path=destination.relative_to(
                                source.parent
                            ).as_posix(),
                            content_hash=hashlib.sha256(
                                destination.read_bytes()
                            ).hexdigest(),
                        )
                    )
                return rendered
        except ExtractionFailure:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExtractionFailure(
                "PDF_RENDER_FAILED",
                f"PDF page rendering failed: {type(exc).__name__}",
            ) from exc
