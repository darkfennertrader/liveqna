from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from .errors import ExtractionFailure


REQUIRED_PARTS = {"[Content_Types].xml", "xl/workbook.xml"}


@dataclass(frozen=True)
class XlsxPackageLimits:
    max_entries: int = 20_000
    max_uncompressed_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_sheets: int = 200
    max_cells: int = 1_000_000


@dataclass(frozen=True)
class XlsxPackageInventory:
    entry_count: int
    uncompressed_bytes: int
    compression_ratio: float
    worksheet_parts: tuple[str, ...]
    external_links: tuple[str, ...]
    embedded_objects: tuple[str, ...]


def validate_xlsx_package(
    path: Path, limits: XlsxPackageLimits
) -> XlsxPackageInventory:
    """Validate the OOXML ZIP before any parser or office process opens it."""

    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = {item.filename for item in infos}
            missing = sorted(REQUIRED_PARTS - names)
            if missing:
                raise ExtractionFailure(
                    "CORRUPT_DOCUMENT",
                    "XLSX package is missing required parts: " + ", ".join(missing),
                )
            if len(infos) > limits.max_entries:
                raise ExtractionFailure(
                    "XLSX_LIMIT_EXCEEDED",
                    f"XLSX package contains {len(infos)} entries; limit is "
                    f"{limits.max_entries}",
                )

            uncompressed = 0
            compressed = 0
            for item in infos:
                supplied = PurePosixPath(item.filename)
                if supplied.is_absolute() or ".." in supplied.parts:
                    raise ExtractionFailure(
                        "CORRUPT_DOCUMENT",
                        "XLSX package contains an unsafe ZIP path",
                    )
                uncompressed += item.file_size
                compressed += item.compress_size
            if uncompressed > limits.max_uncompressed_bytes:
                raise ExtractionFailure(
                    "XLSX_LIMIT_EXCEEDED",
                    "XLSX expanded size exceeds the configured limit",
                )
            ratio = uncompressed / max(compressed, 1)
            if ratio > limits.max_compression_ratio:
                raise ExtractionFailure(
                    "XLSX_LIMIT_EXCEEDED",
                    "XLSX compression ratio exceeds the configured safety limit",
                )

            worksheet_parts = tuple(
                sorted(
                    name
                    for name in names
                    if name.startswith("xl/worksheets/sheet")
                    and name.endswith(".xml")
                )
            )
            if len(worksheet_parts) > limits.max_sheets:
                raise ExtractionFailure(
                    "XLSX_LIMIT_EXCEEDED",
                    f"XLSX contains {len(worksheet_parts)} worksheets; limit is "
                    f"{limits.max_sheets}",
                )
            external_links = tuple(
                sorted(
                    name
                    for name in names
                    if name.startswith("xl/externalLinks/")
                    and name.endswith(".xml")
                )
            )
            embedded_objects = tuple(
                sorted(
                    name
                    for name in names
                    if name.startswith("xl/embeddings/")
                    or name.endswith("vbaProject.bin")
                )
            )
            return XlsxPackageInventory(
                entry_count=len(infos),
                uncompressed_bytes=uncompressed,
                compression_ratio=round(ratio, 3),
                worksheet_parts=worksheet_parts,
                external_links=external_links,
                embedded_objects=embedded_objects,
            )
    except ExtractionFailure:
        raise
    except (BadZipFile, OSError) as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "XLSX package could not be opened"
        ) from exc
