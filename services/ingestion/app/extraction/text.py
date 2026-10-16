from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .common import DocumentBuilder
from .errors import ExtractionFailure
from .models import BlockKind, NormalizedDocument, SourceLocator

TRANSCRIPT_TURN = re.compile(
    r"^(?:\[(?P<bracket_time>\d{1,2}:\d{2}(?::\d{2})?)\]|"
    r"(?P<plain_time>\d{1,2}:\d{2}(?::\d{2})?))?\s*"
    r"(?P<speaker>[^:\n]{1,81}?):\s*(?P<text>.+)$"
)
FRONT_MATTER_BOUNDARY = {"---", "..."}


@dataclass(frozen=True)
class TextExtractionLimits:
    max_bytes: int = 32 * 1024 * 1024
    max_lines: int = 500_000
    max_line_characters: int = 200_000
    max_blocks: int = 100_000
    max_block_characters: int = 2 * 1024 * 1024


def transcript_parts(line: str) -> tuple[str | None, str | None, str]:
    match = TRANSCRIPT_TURN.fullmatch(line.strip())
    if match is None:
        return None, None, line
    timestamp = match.group("bracket_time") or match.group("plain_time")
    return timestamp, match.group("speaker").strip(), match.group("text")


def _limit_failure(limit: str, actual: int, maximum: int) -> ExtractionFailure:
    return ExtractionFailure(
        "TEXT_LIMIT_EXCEEDED",
        f"{limit} limit exceeded: {actual} > {maximum}",
    )


def _read_utf8(path: Path, limits: TextExtractionLimits) -> str:
    byte_count = path.stat().st_size
    if byte_count > limits.max_bytes:
        raise _limit_failure("byte", byte_count, limits.max_bytes)
    try:
        raw = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionFailure(
            "UNSUPPORTED_ENCODING", "text file is not valid UTF-8"
        ) from exc
    if "\x00" in raw:
        raise ExtractionFailure(
            "BINARY_TEXT", "text file contains NUL bytes and appears to be binary"
        )
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise ExtractionFailure("EMPTY_DOCUMENT", "text file is empty")
    lines = normalized.split("\n")
    if len(lines) > limits.max_lines:
        raise _limit_failure("line count", len(lines), limits.max_lines)
    longest_line = max(map(len, lines), default=0)
    if longest_line > limits.max_line_characters:
        raise _limit_failure(
            "line character", longest_line, limits.max_line_characters
        )
    return normalized


def _source(token: Token) -> SourceLocator:
    if token.map is None:
        return SourceLocator()
    start, end = token.map
    return SourceLocator(line_start=start + 1, line_end=max(start + 1, end))


def _source_text(lines: list[str], token: Token) -> str:
    if token.map is None:
        return token.content
    start, end = token.map
    return "\n".join(lines[start:end])


def _inline_token(tokens: list[Token], index: int, close_type: str) -> Token | None:
    cursor = index + 1
    while cursor < len(tokens) and tokens[cursor].type != close_type:
        if tokens[cursor].type == "inline":
            return tokens[cursor]
        cursor += 1
    return None


def _inline_attributes(token: Token | None) -> dict[str, Any]:
    links: list[dict[str, str]] = []
    images: list[dict[str, str]] = []
    children = (token.children or []) if token is not None else []
    for child in children:
        if child.type == "link_open":
            href = child.attrGet("href") or ""
            title = child.attrGet("title") or ""
            links.append({"href": href, "title": title})
        elif child.type == "image":
            src = child.attrGet("src") or ""
            title = child.attrGet("title") or ""
            images.append({"src": src, "alt": child.content, "title": title})
    attributes: dict[str, Any] = {}
    if links:
        attributes["links"] = links
    if images:
        attributes["images"] = images
    return attributes


def _front_matter(lines: list[str]) -> tuple[tuple[int, int] | None, list[str]]:
    if not lines or lines[0].strip() != "---":
        return None, lines
    for index in range(1, min(len(lines), 500)):
        if lines[index].strip() in FRONT_MATTER_BOUNDARY:
            parser_lines = list(lines)
            parser_lines[: index + 1] = [""] * (index + 1)
            return (0, index + 1), parser_lines
    return None, lines


def _close_index(tokens: list[Token], start: int, open_type: str) -> int:
    close_type = open_type.removesuffix("_open") + "_close"
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].type == open_type:
            depth += 1
        elif tokens[index].type == close_type:
            depth -= 1
            if depth == 0:
                return index
    return start


def _enforce_block_limits(
    builder: DocumentBuilder, limits: TextExtractionLimits
) -> None:
    if len(builder.blocks) > limits.max_blocks:
        raise _limit_failure("block count", len(builder.blocks), limits.max_blocks)
    if builder.blocks and len(builder.blocks[-1].text) > limits.max_block_characters:
        raise _limit_failure(
            "block character",
            len(builder.blocks[-1].text),
            limits.max_block_characters,
        )


def _add_markdown_block(
    builder: DocumentBuilder,
    kind: BlockKind,
    text: str,
    source: SourceLocator,
    limits: TextExtractionLimits,
    *,
    attributes: dict[str, Any] | None = None,
    retrieval_eligible: bool = True,
    preserve_whitespace: bool = True,
) -> None:
    builder.add(
        kind,
        text,
        source,
        attributes=attributes,
        retrieval_eligible=retrieval_eligible,
        preserve_whitespace=preserve_whitespace,
    )
    _enforce_block_limits(builder, limits)


def _extract_markdown(
    raw: str,
    builder: DocumentBuilder,
    limits: TextExtractionLimits,
) -> None:
    source_lines = raw.split("\n")
    front_matter, parser_lines = _front_matter(source_lines)
    parser_source = "\n".join(parser_lines)
    if front_matter is not None:
        start, end = front_matter
        _add_markdown_block(
            builder,
            "metadata",
            "\n".join(source_lines[start:end]),
            SourceLocator(line_start=start + 1, line_end=end),
            limits,
            attributes={"format": "front_matter"},
            retrieval_eligible=False,
        )

    parser = MarkdownIt(
        "commonmark", {"html": False, "maxNesting": 100}
    ).enable("table")
    try:
        tokens = parser.parse(parser_source)
    except (MemoryError, RecursionError, ValueError) as exc:
        raise ExtractionFailure(
            "MARKDOWN_PARSE_FAILED", f"Markdown parsing failed: {type(exc).__name__}"
        ) from exc

    image_reference_seen = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.level != 0:
            index += 1
            continue

        if token.type == "heading_open":
            inline = _inline_token(tokens, index, "heading_close")
            attributes = _inline_attributes(inline)
            image_reference_seen = image_reference_seen or bool(attributes.get("images"))
            builder.heading(
                inline.content if inline is not None else _source_text(source_lines, token),
                _source(token),
                level=int(token.tag.removeprefix("h")),
                attributes=attributes,
            )
            _enforce_block_limits(builder, limits)
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            close = _close_index(tokens, index, token.type)
            attributes: dict[str, Any] = {
                "ordered": token.type == "ordered_list_open"
            }
            if token.type == "ordered_list_open" and token.attrGet("start"):
                attributes["start"] = int(token.attrGet("start") or "1")
            _add_markdown_block(
                builder,
                "list",
                _source_text(source_lines, token),
                _source(token),
                limits,
                attributes=attributes,
            )
            index = close
        elif token.type == "blockquote_open":
            close = _close_index(tokens, index, token.type)
            _add_markdown_block(
                builder,
                "blockquote",
                _source_text(source_lines, token),
                _source(token),
                limits,
            )
            index = close
        elif token.type == "table_open":
            close = _close_index(tokens, index, token.type)
            _add_markdown_block(
                builder,
                "table",
                _source_text(source_lines, token),
                _source(token),
                limits,
                attributes={"format": "gfm_pipe_table"},
            )
            index = close
        elif token.type in {"fence", "code_block"}:
            attributes = {
                "language": token.info.strip().split(maxsplit=1)[0]
                if token.info.strip()
                else "",
                "executable": False,
            }
            _add_markdown_block(
                builder,
                "code",
                _source_text(source_lines, token),
                _source(token),
                limits,
                attributes=attributes,
            )
        elif token.type == "html_block":
            _add_markdown_block(
                builder,
                "code",
                _source_text(source_lines, token),
                _source(token),
                limits,
                attributes={"language": "html", "executable": False},
            )
        elif token.type == "paragraph_open":
            inline = _inline_token(tokens, index, "paragraph_close")
            attributes = _inline_attributes(inline)
            image_reference_seen = image_reference_seen or bool(attributes.get("images"))
            _add_markdown_block(
                builder,
                "paragraph",
                _source_text(source_lines, token),
                _source(token),
                limits,
                attributes=attributes,
            )
        index += 1

    if image_reference_seen:
        builder.warnings.append(
            "Markdown image references were retained but image contents were not interpreted."
        )


def _extract_plain_text(
    raw: str,
    builder: DocumentBuilder,
    limits: TextExtractionLimits,
    *,
    transcript: bool,
) -> None:
    lines = raw.split("\n")
    paragraph: list[str] = []
    paragraph_start = 0

    def flush(end_line: int) -> None:
        nonlocal paragraph, paragraph_start
        if not paragraph:
            return
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
                    preserve_whitespace=True,
                )
                _enforce_block_limits(builder, limits)
        else:
            builder.add(
                "paragraph",
                "\n".join(paragraph),
                SourceLocator(line_start=paragraph_start, line_end=end_line),
                preserve_whitespace=True,
            )
            _enforce_block_limits(builder, limits)
        paragraph = []
        paragraph_start = 0

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            flush(index - 1)
        else:
            if not paragraph:
                paragraph_start = index
            paragraph.append(line)
    flush(len(lines))


def extract_text_document(
    path: Path,
    source_filename: str,
    source_format: str,
    content_hash: str,
    *,
    markdown: bool,
    transcript: bool,
    limits: TextExtractionLimits | None = None,
) -> NormalizedDocument:
    effective_limits = limits or TextExtractionLimits()
    raw = _read_utf8(path, effective_limits)
    builder = DocumentBuilder(source_filename, source_format, content_hash)
    if markdown:
        _extract_markdown(raw, builder, effective_limits)
    else:
        _extract_plain_text(
            raw, builder, effective_limits, transcript=transcript
        )
    return builder.build()
