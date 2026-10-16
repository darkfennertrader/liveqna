from __future__ import annotations

from pathlib import Path

import pytest
from app.extraction.errors import ExtractionFailure
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.text import TextExtractionLimits


def extract(path: Path, *, limits: TextExtractionLimits | None = None):
    return ExtractionPipeline(text_limits=limits).extract(
        path,
        path.name,
        "transcript" if path.name.startswith("transcript") else "official",
        "sha256-for-test",
    )


def test_plain_text_accepts_utf8_bom_crlf_and_preserves_layout(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes("\ufeffCafé  plan\r\n  indented detail\r\n".encode())

    first = extract(path)
    second = extract(path)

    assert first == second
    assert first.blocks[0].text == "Café  plan\n  indented detail"
    assert first.blocks[0].source.line_start == 1
    assert first.blocks[0].source.line_end == 2


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"\xff\xfeinvalid", "UNSUPPORTED_ENCODING"),
        (b"plain\x00binary", "BINARY_TEXT"),
    ],
)
def test_text_rejects_invalid_or_binary_content(
    tmp_path: Path, content: bytes, expected_code: str
) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(content)

    with pytest.raises(ExtractionFailure) as raised:
        extract(path)

    assert raised.value.code == expected_code


@pytest.mark.parametrize(
    ("content", "limits"),
    [
        ("four", TextExtractionLimits(max_bytes=3)),
        ("a\nb", TextExtractionLimits(max_lines=1)),
        ("long", TextExtractionLimits(max_line_characters=3)),
        ("one\n\ntwo", TextExtractionLimits(max_blocks=1)),
        ("large", TextExtractionLimits(max_block_characters=4)),
    ],
)
def test_text_resource_limits_fail_closed(
    tmp_path: Path, content: str, limits: TextExtractionLimits
) -> None:
    path = tmp_path / "bounded.txt"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ExtractionFailure) as raised:
        extract(path, limits=limits)

    assert raised.value.code == "TEXT_LIMIT_EXCEEDED"


def test_markdown_uses_commonmark_structure_and_source_lines(tmp_path: Path) -> None:
    path = tmp_path / "structured.markdown"
    path.write_text(
        "Document title\n==============\n\n"
        "- first\n- second\n\n"
        "> quoted decision\n\n"
        "| Name | Value |\n| --- | --- |\n| A | 1 |\n",
        encoding="utf-8",
    )

    normalized = extract(path)

    assert [block.kind for block in normalized.blocks] == [
        "heading",
        "list",
        "blockquote",
        "table",
    ]
    assert normalized.blocks[0].source.line_start == 1
    assert normalized.blocks[0].source.line_end == 2
    assert normalized.blocks[1].attributes == {"ordered": False}
    assert normalized.blocks[-1].source.line_start == 9
    assert normalized.blocks[-1].attributes["format"] == "gfm_pipe_table"


def test_markdown_fences_do_not_create_false_headings_or_tables(tmp_path: Path) -> None:
    path = tmp_path / "code.md"
    path.write_text(
        "```python\n# not a heading\n| not | a table |\n```\n\n"
        "this | is ordinary prose\n",
        encoding="utf-8",
    )

    normalized = extract(path)

    assert [block.kind for block in normalized.blocks] == ["code", "paragraph"]
    assert normalized.blocks[0].attributes == {
        "language": "python",
        "executable": False,
    }
    assert "# not a heading" in normalized.blocks[0].text
    assert normalized.blocks[1].text == "this | is ordinary prose"


def test_markdown_front_matter_links_and_images_are_auditable(tmp_path: Path) -> None:
    path = tmp_path / "references.md"
    path.write_text(
        "---\nowner: architecture\nclassification: internal\n---\n\n"
        "Read [the decision](https://example.test/decision) and "
        "![diagram](images/flow.png).\n",
        encoding="utf-8",
    )

    normalized = extract(path)

    assert [block.kind for block in normalized.blocks] == ["metadata", "paragraph"]
    assert normalized.blocks[0].retrieval_eligible is False
    assert normalized.blocks[0].source.line_start == 1
    assert normalized.blocks[0].source.line_end == 4
    assert normalized.blocks[1].attributes["links"][0]["href"] == (
        "https://example.test/decision"
    )
    assert normalized.blocks[1].attributes["images"][0]["src"] == (
        "images/flow.png"
    )
    assert normalized.quality.status == "WARNING"
    assert "not interpreted" in normalized.quality.warnings[0]


def test_transcript_accepts_unicode_speaker_names(tmp_path: Path) -> None:
    path = tmp_path / "transcript.txt"
    path.write_text("[09:15] Élodie Černá: Rozhodnutí schváleno.\n", encoding="utf-8")

    normalized = extract(path)

    assert normalized.blocks[0].kind == "transcript_turn"
    assert normalized.blocks[0].source.speaker == "Élodie Černá"
    assert normalized.blocks[0].source.timestamp == "09:15"
