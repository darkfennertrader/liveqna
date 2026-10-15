from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import pymupdf
from PIL import Image

from .errors import ExtractionFailure


@dataclass(frozen=True)
class RenderedWorksheet:
    worksheet_index: int
    title: str
    path: Path
    relative_path: str
    content_hash: str
    page_count: int
    pages_truncated: bool


@dataclass(frozen=True)
class XlsxRenderResult:
    worksheets: list[RenderedWorksheet]
    recalculated_workbook: Path


@runtime_checkable
class XlsxRenderer(Protocol):
    version: str

    def render(
        self, source: Path, worksheet_indexes: list[int]
    ) -> XlsxRenderResult: ...


class LibreOfficeXlsxRenderer:
    version = "libreoffice-uno-pymupdf-v1"

    def __init__(
        self,
        *,
        soffice_binary: str = "soffice",
        uno_python: str = "/usr/bin/python3",
        helper_script: str = "/app/scripts/xlsx_libreoffice.py",
        dpi: int = 144,
        timeout_seconds: float = 180.0,
        max_pages_per_sheet: int = 8,
    ) -> None:
        self._soffice_binary = soffice_binary
        self._uno_python = uno_python
        self._helper_script = helper_script
        self._dpi = dpi
        self._timeout_seconds = timeout_seconds
        self._max_pages_per_sheet = max_pages_per_sheet

    def render(
        self, source: Path, worksheet_indexes: list[int]
    ) -> XlsxRenderResult:
        selected = sorted(set(worksheet_indexes))
        if not selected:
            raise ExtractionFailure(
                "XLSX_RENDER_FAILED", "no worksheets were selected for rendering"
            )
        source = source.resolve()
        output_directory = source.parent / "worksheets"
        output_directory.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=".xlsx-render-", dir=source.parent
            ) as temporary_name:
                temporary = Path(temporary_name)
                profile = temporary / "libreoffice-profile"
                runtime = temporary / "runtime"
                runtime.mkdir(mode=0o700)
                command = [
                    self._uno_python,
                    self._helper_script,
                    "--source",
                    str(source),
                    "--output-dir",
                    str(temporary),
                    "--profile-dir",
                    str(profile),
                    "--soffice",
                    self._soffice_binary,
                    "--timeout",
                    str(self._timeout_seconds),
                ]
                for worksheet_index in selected:
                    command.extend(["--sheet-index", str(worksheet_index)])
                environment = {
                    **os.environ,
                    "XDG_RUNTIME_DIR": str(runtime),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "XDG_CACHE_HOME": str(temporary / "cache"),
                }
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
                    raise ExtractionFailure(
                        "XLSX_RENDER_FAILED",
                        "LibreOffice worksheet rendering failed"
                        + (f": {detail[-500:]}" if detail else ""),
                    )
                manifest_path = temporary / "manifest.json"
                if not manifest_path.is_file():
                    raise ExtractionFailure(
                        "XLSX_RENDER_FAILED",
                        "LibreOffice did not produce the worksheet manifest",
                    )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                recalculated_source = temporary / manifest["recalculated_workbook"]
                recalculated_destination = output_directory / "recalculated.xlsx"
                os.replace(recalculated_source, recalculated_destination)

                rendered = []
                for item in manifest["worksheets"]:
                    worksheet_index = int(item["worksheet_index"])
                    pdf = temporary / item["pdf"]
                    destination = (
                        output_directory / f"worksheet-{worksheet_index:04d}.png"
                    )
                    page_count, truncated = self._render_contact_sheet(pdf, destination)
                    rendered.append(
                        RenderedWorksheet(
                            worksheet_index=worksheet_index,
                            title=str(item["title"]),
                            path=destination,
                            relative_path=destination.relative_to(
                                source.parent
                            ).as_posix(),
                            content_hash=hashlib.sha256(
                                destination.read_bytes()
                            ).hexdigest(),
                            page_count=page_count,
                            pages_truncated=truncated,
                        )
                    )
                return XlsxRenderResult(
                    worksheets=rendered,
                    recalculated_workbook=recalculated_destination,
                )
        except ExtractionFailure:
            raise
        except (OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
            raise ExtractionFailure(
                "XLSX_RENDER_FAILED",
                f"worksheet rendering failed: {type(exc).__name__}",
            ) from exc

    def _render_contact_sheet(self, pdf: Path, destination: Path) -> tuple[int, bool]:
        document = pymupdf.open(pdf)
        page_count = document.page_count
        selected_count = min(page_count, self._max_pages_per_sheet)
        images: list[Image.Image] = []
        for page_number in range(selected_count):
            pixmap = document[page_number].get_pixmap(
                dpi=self._dpi,
                colorspace=pymupdf.csRGB,
                alpha=False,
                annots=True,
            )
            images.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
        document.close()
        if not images:
            raise ExtractionFailure(
                "XLSX_RENDER_FAILED", "worksheet PDF contains no pages"
            )
        max_width = min(max(image.width for image in images), 2400)
        resized = []
        for image in images:
            if image.width > max_width:
                height = max(1, round(image.height * max_width / image.width))
                image = image.resize((max_width, height), Image.Resampling.LANCZOS)
            resized.append(image)
        gap = 24
        contact = Image.new(
            "RGB",
            (max(image.width for image in resized), sum(image.height for image in resized) + gap * (len(resized) - 1)),
            "white",
        )
        y = 0
        for image in resized:
            contact.paste(image, (0, y))
            y += image.height + gap
        contact.save(destination, format="PNG", optimize=True)
        return page_count, page_count > selected_count
