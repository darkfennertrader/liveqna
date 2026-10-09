"""Common interface every transcription backend implements.

A backend owns its own buffering/decoding strategy and simply emits
TranscriptEvent objects onto an asyncio.Queue as they become available.
`feed()` is called for every incoming PCM16 chunk from `capture`; `events()`
yields events for the main app to relay to the UI; `close()` releases any
resident resources (e.g. frees GPU VRAM for the local backend).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class TranscriptEvent:
    type: str  # "partial" | "final"
    text: str
    ts: float = field(default_factory=time.time)
    segment_id: int | None = None
    speaker_id: str | None = None
    is_overlap: bool = False

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "text": self.text,
            "ts": self.ts,
            "segment_id": self.segment_id,
            "speaker_id": self.speaker_id,
            "is_overlap": self.is_overlap,
        }


class TranscriberBackend(ABC):
    @abstractmethod
    async def feed(self, pcm_chunk: bytes) -> None:
        """Accept a chunk of raw PCM16 mono audio."""

    @abstractmethod
    async def events(self) -> AsyncIterator[TranscriptEvent]:
        """Yield transcript events as they become available. Runs until
        `close()` is called."""

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by this backend (GPU memory, open
        connections, etc.)."""
