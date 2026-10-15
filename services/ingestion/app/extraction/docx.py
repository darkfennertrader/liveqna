from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .common import DocumentBuilder
from .errors import ExtractionFailure
from .models import NormalizedDocument, SourceLocator
from .text import transcript_parts


def extract_docx(
    path: Path,
    source_filename: str,
    content_hash: str,
    *,
    transcript: bool,
) -> NormalizedDocument:
    try:
        document = Document(path)
    except Exception as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "DOCX file could not be opened"
        ) from exc

    builder = DocumentBuilder(source_filename, "docx", content_hash)
    paragraph_number = 0
    table_number = 0
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            paragraph_number += 1
            text = item.text.strip()
            if not text:
                continue
            style_name = item.style.name if item.style is not None else ""
            if style_name.startswith("Heading"):
                suffix = style_name.removeprefix("Heading").strip()
                level = int(suffix) if suffix.isdigit() else 1
                builder.heading(
                    text,
                    SourceLocator(paragraph=paragraph_number),
                    level=level,
                )
            elif transcript:
                timestamp, speaker, body = transcript_parts(text)
                builder.add(
                    "transcript_turn" if speaker else "paragraph",
                    body,
                    SourceLocator(
                        paragraph=paragraph_number,
                        timestamp=timestamp,
                        speaker=speaker,
                    ),
                )
            else:
                builder.add(
                    "paragraph", text, SourceLocator(paragraph=paragraph_number)
                )
        elif isinstance(item, Table):
            table_number += 1
            rows = [
                "\t".join(cell.text.strip() for cell in row.cells)
                for row in item.rows
            ]
            builder.add(
                "table",
                "\n".join(row for row in rows if row.strip()),
                SourceLocator(table=table_number),
            )
    return builder.build()
