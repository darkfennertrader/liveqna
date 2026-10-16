from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import pymupdf

from .errors import ExtractionFailure
from .pdf_renderer import RenderedPage


@dataclass(frozen=True)
class DocxRenderResult:
    pdf_path: Path
    pages: list[RenderedPage]


@runtime_checkable
class DocxRenderer(Protocol):
    version: str

    def render(self, source: Path, *, max_pages: int) -> DocxRenderResult: ...


class LibreOfficeDocxRenderer:
    version = "libreoffice-writer-pymupdf-v1"

    def __init__(
        self,
        *,
        soffice_binary: str = "soffice",
        dpi: int = 144,
        timeout_seconds: float = 180.0,
    ) -> None:
        if dpi <= 0:
            raise ValueError("DOCX render DPI must be positive")
        self._soffice_binary = soffice_binary
        self._dpi = dpi
        self._timeout_seconds = timeout_seconds

    def render(self, source: Path, *, max_pages: int) -> DocxRenderResult:
        source = source.resolve()
        pages_directory = source.parent / "pages"
        rendered_directory = source.parent / "rendered"
        pages_directory.mkdir(parents=True, exist_ok=True)
        rendered_directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=".docx-render-", dir=source.parent
            ) as temporary_name:
                temporary = Path(temporary_name)
                profile = temporary / "libreoffice-profile"
                profile.mkdir()
                runtime = temporary / "runtime"
                runtime.mkdir(mode=0o700)
                home = temporary / "home"
                home.mkdir(mode=0o700)
                environment = {
                    **os.environ,
                    "HOME": str(home),
                    "XDG_RUNTIME_DIR": str(runtime),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "XDG_CACHE_HOME": str(temporary / "cache"),
                }
                completed = subprocess.run(
                    [
                        self._soffice_binary,
                        f"-env:UserInstallation={profile.as_uri()}",
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nolockcheck",
                        "--nofirststartwizard",
                        "--convert-to",
                        "pdf:writer_pdf_Export",
                        "--outdir",
                        str(temporary),
                        str(source),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    env=environment,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout).strip()
                    raise ExtractionFailure(
                        "DOCX_RENDER_FAILED",
                        "LibreOffice DOCX-to-PDF conversion failed"
                        + (
                            f": {detail[-500:]}"
                            if detail
                            else f" with exit code {completed.returncode}"
                        ),
                    )
                temporary_pdf = temporary / f"{source.stem}.pdf"
                if not temporary_pdf.is_file():
                    raise ExtractionFailure(
                        "DOCX_RENDER_FAILED",
                        "LibreOffice did not produce the expected DOCX PDF",
                    )
                destination_pdf = rendered_directory / "document.pdf"
                os.replace(temporary_pdf, destination_pdf)

                document = pymupdf.open(destination_pdf)
                if document.needs_pass:
                    document.close()
                    raise ExtractionFailure(
                        "DOCX_RENDER_FAILED", "rendered DOCX PDF is encrypted"
                    )
                if document.page_count == 0:
                    document.close()
                    raise ExtractionFailure(
                        "DOCX_RENDER_FAILED", "rendered DOCX contains no pages"
                    )
                if document.page_count > max_pages:
                    page_count = document.page_count
                    document.close()
                    raise ExtractionFailure(
                        "DOCX_LIMIT_EXCEEDED",
                        f"rendered DOCX contains {page_count} pages; limit is "
                        f"{max_pages}",
                    )
                rendered: list[RenderedPage] = []
                for page_number, page in enumerate(document, start=1):
                    temporary_image = temporary / f"page-{page_number:04d}.png"
                    destination_image = pages_directory / temporary_image.name
                    pixmap = page.get_pixmap(
                        dpi=self._dpi,
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                        annots=True,
                    )
                    pixmap.save(temporary_image)
                    os.replace(temporary_image, destination_image)
                    rendered.append(
                        RenderedPage(
                            page_number=page_number,
                            path=destination_image,
                            relative_path=destination_image.relative_to(
                                source.parent
                            ).as_posix(),
                            content_hash=hashlib.sha256(
                                destination_image.read_bytes()
                            ).hexdigest(),
                        )
                    )
                document.close()
                return DocxRenderResult(pdf_path=destination_pdf, pages=rendered)
        except ExtractionFailure:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExtractionFailure(
                "DOCX_RENDER_FAILED",
                f"DOCX rendering failed: {type(exc).__name__}",
            ) from exc
