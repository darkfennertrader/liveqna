from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from defusedxml.ElementTree import fromstring

from .errors import ExtractionFailure

REQUIRED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
}
RELATIONSHIP_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class DocxPackageLimits:
    max_entries: int = 20_000
    max_compressed_bytes: int = 128 * 1024 * 1024
    max_uncompressed_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_paragraphs: int = 200_000
    max_tables: int = 20_000
    max_pages: int = 500


@dataclass(frozen=True)
class DocxPackageInventory:
    entry_count: int
    compressed_bytes: int
    uncompressed_bytes: int
    compression_ratio: float
    story_parts: tuple[str, ...]
    external_links: tuple[str, ...]
    embedded_objects: tuple[str, ...]
    paragraph_count: int
    table_count: int


def validate_docx_package(
    path: Path, limits: DocxPackageLimits
) -> DocxPackageInventory:
    """Reject unsafe or unreasonable OOXML packages before parsing/rendering."""

    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            unique_names = set(names)
            missing = sorted(REQUIRED_PARTS - unique_names)
            if missing:
                raise ExtractionFailure(
                    "CORRUPT_DOCUMENT",
                    "DOCX package is missing required parts: " + ", ".join(missing),
                )
            if len(unique_names) != len(names):
                raise ExtractionFailure(
                    "CORRUPT_DOCUMENT", "DOCX package contains duplicate ZIP entries"
                )
            if len(infos) > limits.max_entries:
                raise ExtractionFailure(
                    "DOCX_LIMIT_EXCEEDED",
                    f"DOCX package contains {len(infos)} entries; limit is "
                    f"{limits.max_entries}",
                )

            compressed = 0
            uncompressed = 0
            for item in infos:
                supplied = PurePosixPath(item.filename)
                if (
                    supplied.is_absolute()
                    or ".." in supplied.parts
                    or "\\" in item.filename
                ):
                    raise ExtractionFailure(
                        "CORRUPT_DOCUMENT",
                        "DOCX package contains an unsafe ZIP path",
                    )
                if item.flag_bits & 0x1:
                    raise ExtractionFailure(
                        "CORRUPT_DOCUMENT",
                        "DOCX package contains an encrypted ZIP entry",
                    )
                compressed += item.compress_size
                uncompressed += item.file_size
            if compressed > limits.max_compressed_bytes:
                raise ExtractionFailure(
                    "DOCX_LIMIT_EXCEEDED",
                    "DOCX compressed size exceeds the configured limit",
                )
            if uncompressed > limits.max_uncompressed_bytes:
                raise ExtractionFailure(
                    "DOCX_LIMIT_EXCEEDED",
                    "DOCX expanded size exceeds the configured limit",
                )
            ratio = uncompressed / max(compressed, 1)
            if ratio > limits.max_compression_ratio:
                raise ExtractionFailure(
                    "DOCX_LIMIT_EXCEEDED",
                    "DOCX compression ratio exceeds the configured safety limit",
                )
            damaged = archive.testzip()
            if damaged is not None:
                raise ExtractionFailure(
                    "CORRUPT_DOCUMENT",
                    f"DOCX package CRC validation failed for {damaged}",
                )

            document_root = _parse_xml(archive, "word/document.xml")
            paragraphs = sum(1 for _ in document_root.iter(WORD_NS + "p"))
            tables = sum(1 for _ in document_root.iter(WORD_NS + "tbl"))
            if paragraphs > limits.max_paragraphs:
                raise ExtractionFailure(
                    "DOCX_LIMIT_EXCEEDED",
                    f"DOCX contains {paragraphs} paragraphs; limit is "
                    f"{limits.max_paragraphs}",
                )
            if tables > limits.max_tables:
                raise ExtractionFailure(
                    "DOCX_LIMIT_EXCEEDED",
                    f"DOCX contains {tables} tables; limit is {limits.max_tables}",
                )

            story_parts = tuple(
                sorted(
                    [
                        name
                        for name in unique_names
                        if name == "word/document.xml"
                        or name == "word/comments.xml"
                        or name == "word/footnotes.xml"
                        or name == "word/endnotes.xml"
                        or name.startswith(("word/header", "word/footer"))
                    ],
                    key=_story_sort_key,
                )
            )
            external_links: set[str] = set()
            for relationship_part in (
                name for name in unique_names if name.endswith(".rels")
            ):
                root = _parse_xml(archive, relationship_part)
                for relationship in root.iter(RELATIONSHIP_NS + "Relationship"):
                    if relationship.attrib.get("TargetMode") == "External":
                        target = relationship.attrib.get("Target", "").strip()
                        if target:
                            external_links.add(target)
            active_content = tuple(
                sorted(
                    name
                    for name in unique_names
                    if _is_active_content(name)
                )
            )
            if active_content:
                raise ExtractionFailure(
                    "UNSAFE_DOCUMENT",
                    "DOCX package contains active content that is not rendered: "
                    + ", ".join(active_content),
                )
            embedded_objects = tuple(
                sorted(
                    name
                    for name in unique_names
                    if name.startswith("word/embeddings/")
                )
            )
            return DocxPackageInventory(
                entry_count=len(infos),
                compressed_bytes=compressed,
                uncompressed_bytes=uncompressed,
                compression_ratio=round(ratio, 3),
                story_parts=story_parts,
                external_links=tuple(sorted(external_links)),
                embedded_objects=embedded_objects,
                paragraph_count=paragraphs,
                table_count=tables,
            )
    except ExtractionFailure:
        raise
    except (BadZipFile, OSError, ValueError) as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "DOCX package could not be opened safely"
        ) from exc


def _parse_xml(archive: ZipFile, name: str):
    try:
        return fromstring(archive.read(name))
    except Exception as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", f"DOCX XML part is invalid: {name}"
        ) from exc


def _story_sort_key(name: str) -> tuple[int, str]:
    if name == "word/document.xml":
        return (0, name)
    if name.startswith("word/header"):
        return (1, name)
    if name.startswith("word/footer"):
        return (2, name)
    if name == "word/footnotes.xml":
        return (3, name)
    if name == "word/endnotes.xml":
        return (4, name)
    if name == "word/comments.xml":
        return (5, name)
    return (6, name)


def _is_active_content(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered.endswith("vbaproject.bin")
        or lowered.startswith(("word/activex/", "word/afchunk"))
    )
