from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .errors import ExtractionFailure


PAGE_NUMBER = re.compile(r"-(?P<number>\d+)\.png$")


@dataclass(frozen=True)
class RenderedSlide:
    slide_number: int
    path: Path
    relative_path: str
    content_hash: str


@runtime_checkable
class PptxRenderer(Protocol):
    version: str

    def render(self, source: Path, expected_slides: int) -> list[RenderedSlide]: ...


class LibreOfficePptxRenderer:
    version = "libreoffice-pdftoppm-v1"

    def __init__(
        self,
        *,
        soffice_binary: str = "soffice",
        pdftoppm_binary: str = "pdftoppm",
        dpi: int = 144,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._soffice_binary = soffice_binary
        self._pdftoppm_binary = pdftoppm_binary
        self._dpi = dpi
        self._timeout_seconds = timeout_seconds

    def render(self, source: Path, expected_slides: int) -> list[RenderedSlide]:
        source = source.resolve()
        output_directory = source.parent / "slides"
        output_directory.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.TemporaryDirectory(
                prefix=".pptx-render-", dir=source.parent
            ) as temporary_name:
                temporary = Path(temporary_name)
                profile = temporary / "libreoffice-profile"
                profile.mkdir()
                runtime = temporary / "runtime"
                runtime.mkdir(mode=0o700)
                office_environment = {
                    **os.environ,
                    "XDG_RUNTIME_DIR": str(runtime),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "XDG_CACHE_HOME": str(temporary / "cache"),
                }
                self._run(
                    [
                        self._soffice_binary,
                        f"-env:UserInstallation={profile.as_uri()}",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(temporary),
                        str(source),
                    ],
                    "PPTX-to-PDF conversion",
                    environment=office_environment,
                )
                pdf = temporary / f"{source.stem}.pdf"
                if not pdf.is_file():
                    raise ExtractionFailure(
                        "PPTX_RENDER_FAILED",
                        "LibreOffice did not produce the expected PDF",
                    )
                self._run(
                    [
                        self._pdftoppm_binary,
                        "-png",
                        "-r",
                        str(self._dpi),
                        str(pdf),
                        str(temporary / "slide"),
                    ],
                    "PDF-to-PNG conversion",
                )
                generated = sorted(
                    temporary.glob("slide-*.png"), key=_page_number
                )
                if len(generated) != expected_slides:
                    raise ExtractionFailure(
                        "PPTX_RENDER_FAILED",
                        "rendered slide count does not match the presentation: "
                        f"expected {expected_slides}, received {len(generated)}",
                    )

                rendered: list[RenderedSlide] = []
                for slide_number, temporary_image in enumerate(generated, start=1):
                    destination = output_directory / f"slide-{slide_number:04d}.png"
                    os.replace(temporary_image, destination)
                    rendered.append(
                        RenderedSlide(
                            slide_number=slide_number,
                            path=destination,
                            relative_path=destination.relative_to(source.parent).as_posix(),
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
                "PPTX_RENDER_FAILED",
                f"slide rendering failed: {type(exc).__name__}",
            ) from exc

    def _run(
        self,
        command: list[str],
        operation: str,
        *,
        environment: dict[str, str] | None = None,
    ) -> None:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            env=environment,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            if len(detail) > 500:
                detail = detail[-500:]
            raise ExtractionFailure(
                "PPTX_RENDER_FAILED",
                f"{operation} failed" + (f": {detail}" if detail else ""),
            )


def _page_number(path: Path) -> int:
    match = PAGE_NUMBER.search(path.name)
    if match is None:
        return 0
    return int(match.group("number"))
