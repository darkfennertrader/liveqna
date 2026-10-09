"""Common interface every speaker-embedding backend implements.

Backends are swappable at runtime (mirrors the stt service's
TranscriberBackend pattern): each produces a 1-D embedding vector per
utterance, compared downstream via cosine similarity in clustering.py.
Different backends operate on different similarity scales -- see each
backend's module docstring and the default similarity_threshold recorded
alongside it. Switching backends does NOT carry over a sensible threshold
automatically; the caller (main.py) resets to the new backend's default
unless the user explicitly overrides it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class EmbeddingBackend(ABC):
    #: Sensible default similarity_threshold for this backend's score
    #: scale, calibrated against real same-speaker vs different-speaker
    #: audio (see conversation/validation history). Not interchangeable
    #: between backends.
    default_similarity_threshold: float = 0.5

    @abstractmethod
    def embed(self, pcm16_mono: bytes) -> np.ndarray:
        """Return a 1-D embedding vector for a chunk of raw PCM16 mono
        16kHz audio."""

    def close(self) -> None:
        """Release any resources (GPU memory, etc.). Default no-op."""
