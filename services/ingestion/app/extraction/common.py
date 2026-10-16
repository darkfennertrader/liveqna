from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ExtractionFailure
from .models import (
    BlockKind,
    ExtractionQuality,
    NormalizedBlock,
    NormalizedDocument,
    SourceLocator,
)

WHITESPACE = re.compile(r" {2,}")


def clean_text(value: str) -> str:
    lines = [WHITESPACE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


@dataclass
class DocumentBuilder:
    source_filename: str
    source_format: str
    content_hash: str
    blocks: list[NormalizedBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)

    def heading(
        self,
        text: str,
        source: SourceLocator,
        level: int = 1,
        *,
        retrieval_eligible: bool = True,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        cleaned = clean_text(text)
        if not cleaned:
            return
        safe_level = min(max(level, 1), 6)
        self.section_path = self.section_path[: safe_level - 1]
        self.section_path.append(cleaned)
        self.add(
            "heading",
            cleaned,
            source,
            retrieval_eligible=retrieval_eligible,
            attributes=attributes,
        )

    def add(
        self,
        kind: BlockKind,
        text: str,
        source: SourceLocator,
        section_path: list[str] | None = None,
        *,
        retrieval_eligible: bool = True,
        attributes: dict[str, Any] | None = None,
        preserve_whitespace: bool = False,
    ) -> None:
        cleaned = text.strip("\n") if preserve_whitespace else clean_text(text)
        if not cleaned.strip():
            return
        self.blocks.append(
            NormalizedBlock(
                id=f"b{len(self.blocks) + 1:06d}",
                kind=kind,
                text=cleaned,
                section_path=list(
                    self.section_path if section_path is None else section_path
                ),
                source=source,
                retrieval_eligible=retrieval_eligible,
                attributes=attributes or {},
            )
        )

    def build(self) -> NormalizedDocument:
        if not self.blocks:
            raise ExtractionFailure(
                "EMPTY_DOCUMENT", "no usable text was extracted"
            )
        character_count = sum(len(block.text) for block in self.blocks)
        return NormalizedDocument(
            source_filename=self.source_filename,
            source_format=self.source_format,
            content_hash=self.content_hash,
            blocks=self.blocks,
            quality=ExtractionQuality(
                status="WARNING" if self.warnings else "PASS",
                block_count=len(self.blocks),
                text_character_count=character_count,
                warnings=self.warnings,
            ),
        )
