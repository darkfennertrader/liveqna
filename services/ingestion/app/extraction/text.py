from __future__ import annotations

import re
from pathlib import Path

from .common import DocumentBuilder
from .errors import ExtractionFailure
from .models import NormalizedDocument, SourceLocator


MARKDOWN_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
TRANSCRIPT_TURN = re.compile(
    r"^(?:\[(?P<bracket_time>\d{1,2}:\d{2}(?::\d{2})?)\]|"
    r"(?P<plain_time>\d{1,2}:\d{2}(?::\d{2})?))?\s*"
    r"(?P<speaker>[A-Za-z][^:\n]{0,80}):\s*(?P<text>.+)$"
)


def transcript_parts(line: str) -> tuple[str | None, str | None, str]:
    match = TRANSCRIPT_TURN.fullmatch(line.strip())
    if match is None:
        return None, None, line
    timestamp = match.group("bracket_time") or match.group("plain_time")
    return timestamp, match.group("speaker").strip(), match.group("text")


def extract_text_document(
    path: Path,
    source_filename: str,
    source_format: str,
    content_hash: str,
    *,
    markdown: bool,
    transcript: bool,
) -> NormalizedDocument:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionFailure(
            "UNSUPPORTED_ENCODING", "text file is not valid UTF-8"
        ) from exc
    if not raw.strip():
        raise ExtractionFailure("EMPTY_DOCUMENT", "text file is empty")

    builder = DocumentBuilder(source_filename, source_format, content_hash)
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraph: list[str] = []
    paragraph_start = 0

    def flush(end_line: int) -> None:
        nonlocal paragraph, paragraph_start
        if not paragraph:
            return
        text = "\n".join(paragraph)
        if transcript:
            for offset, turn in enumerate(paragraph):
                timestamp, speaker, body = transcript_parts(turn)
                builder.add(
                    "transcript_turn" if speaker else "paragraph",
                    body,
                    SourceLocator(
                        line_start=paragraph_start + offset,
                        line_end=paragraph_start + offset,
                        timestamp=timestamp,
                        speaker=speaker,
                    ),
                )
        else:
            builder.add(
                "paragraph",
                text,
                SourceLocator(line_start=paragraph_start, line_end=end_line),
            )
        paragraph = []
        paragraph_start = 0

    index = 0
    while index < len(lines):
        line_number = index + 1
        line = lines[index]
        stripped = line.strip()
        heading = MARKDOWN_HEADING.fullmatch(stripped) if markdown else None
        if heading is not None:
            flush(line_number - 1)
            builder.heading(
                heading.group("title"),
                SourceLocator(line_start=line_number, line_end=line_number),
                level=len(heading.group("marks")),
            )
            index += 1
            continue
        if markdown and stripped.startswith("|"):
            flush(line_number - 1)
            table_lines: list[str] = []
            table_start = line_number
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            builder.add(
                "table",
                "\n".join(table_lines),
                SourceLocator(line_start=table_start, line_end=index),
            )
            continue
        if not stripped:
            flush(line_number - 1)
        else:
            if not paragraph:
                paragraph_start = line_number
            paragraph.append(stripped)
        index += 1
    flush(len(lines))
    return builder.build()
