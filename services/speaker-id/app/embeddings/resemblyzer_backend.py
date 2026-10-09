"""Resemblyzer-based speaker embedding backend.

Fast and lightweight (GE2E architecture), but was observed in practice
(see conversation history) to produce too-similar scores between
genuinely different speakers when background music/production audio is
shared across them (e.g. video content) -- different-speaker pairs can
score ~0.6-0.65, uncomfortably close to same-speaker scores (~0.85-0.95),
making the similarity_threshold hard to tune without causing false
merges. Kept available as a lightweight option; ECAPA-TDNN is the default
for better discrimination.
"""

from __future__ import annotations

import logging

import numpy as np
from resemblyzer import VoiceEncoder

from .base import EmbeddingBackend

logger = logging.getLogger("speaker_id.embeddings.resemblyzer")


def _pcm16_to_float_wav(pcm16_mono: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm16_mono, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    return audio


class ResemblyzerBackend(EmbeddingBackend):
    # Calibrated against real same-speaker (~0.82-0.92) vs different-speaker
    # (~0.60-0.65) audio.
    default_similarity_threshold = 0.78

    def __init__(self, device: str = "cuda") -> None:
        logger.info("loading Resemblyzer voice encoder on %s", device)
        self._encoder = VoiceEncoder(device)
        logger.info("Resemblyzer voice encoder loaded")

    def embed(self, pcm16_mono: bytes) -> np.ndarray:
        wav = _pcm16_to_float_wav(pcm16_mono)
        return self._encoder.embed_utterance(wav)

    def close(self) -> None:
        del self._encoder
        import gc
        gc.collect()
