from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import pymupdf

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


class PyMuPdfRenderer:
    version = "pymupdf-png-v1"

    def __init__(self, *, dpi: int = 144) -> None:
        if dpi <= 0:
            raise ValueError("PDF render DPI must be positive")
        self._dpi = dpi

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
            document = pymupdf.open(source)
            if document.page_count != expected_pages:
                raise ExtractionFailure(
                    "PDF_RENDER_FAILED",
                    "PDF page count changed between extraction and rendering",
                )
            with tempfile.TemporaryDirectory(
                prefix=".pdf-render-", dir=source.parent
            ) as temporary_name:
                temporary = Path(temporary_name)
                rendered: list[RenderedPage] = []
                for page_number in selected:
                    destination = output_directory / f"page-{page_number:04d}.png"
                    temporary_image = temporary / destination.name
                    pixmap = document[page_number - 1].get_pixmap(
                        dpi=self._dpi,
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                        annots=True,
                    )
                    pixmap.save(temporary_image)
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
            document.close()
            return rendered
        except ExtractionFailure:
            raise
        except Exception as exc:
            raise ExtractionFailure(
                "PDF_RENDER_FAILED",
                f"PDF page rendering failed: {type(exc).__name__}",
            ) from exc
