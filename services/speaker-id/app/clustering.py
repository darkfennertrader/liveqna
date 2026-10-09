"""Online speaker clustering using multi-reference voice embeddings.

Design rationale (see conversation history): a single running centroid per
speaker is fragile to natural voice variability (distance from mic, tone,
background noise) -- a borderline utterance can fall just under the
similarity threshold against the average and incorrectly mint a new
speaker. Instead, each known speaker keeps a small bounded buffer of its
most recent embeddings; a new utterance is compared against every sample
of every known speaker and matched against the single best score, which
is far more forgiving of natural variation in any one utterance.

This module holds state for exactly one capture session. It is cleared
whenever the session's /reset endpoint is called (new capture started).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np


@dataclass
class SpeakerProfile:
    speaker_id: str
    embeddings: list[np.ndarray] = field(default_factory=list)
    utterance_count: int = 0

    def add(self, embedding: np.ndarray, max_samples: int) -> None:
        self.embeddings.append(embedding)
        if len(self.embeddings) > max_samples:
            self.embeddings.pop(0)
        self.utterance_count += 1


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


class SpeakerClusterer:
    def __init__(
        self,
        similarity_threshold: float = 0.78,
        max_samples_per_speaker: int = 8,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.max_samples_per_speaker = max_samples_per_speaker
        self._speakers: dict[str, SpeakerProfile] = {}
        self._id_counter = itertools.count(1)
        # Manual merge map: speaker_id -> canonical speaker_id it has been
        # merged into. Resolved on every lookup so chained merges collapse
        # to their final target.
        self._merge_map: dict[str, str] = {}

    def set_params(self, similarity_threshold: float | None = None,
                    max_samples_per_speaker: int | None = None) -> None:
        if similarity_threshold is not None:
            self.similarity_threshold = similarity_threshold
        if max_samples_per_speaker is not None:
            self.max_samples_per_speaker = max_samples_per_speaker

    def resolve(self, speaker_id: str) -> str:
        """Follow the merge chain to the canonical speaker_id."""
        seen = set()
        while speaker_id in self._merge_map and speaker_id not in seen:
            seen.add(speaker_id)
            speaker_id = self._merge_map[speaker_id]
        return speaker_id

    def merge(self, source_id: str, target_id: str) -> None:
        """Merge `source_id` into `target_id` going forward (manual
        correction from the UI for a false-split)."""
        source_id = self.resolve(source_id)
        target_id = self.resolve(target_id)
        if source_id == target_id:
            return
        self._merge_map[source_id] = target_id
        # Fold the source's samples into the target so future comparisons
        # benefit from both speakers' reference embeddings.
        source = self._speakers.get(source_id)
        target = self._speakers.get(target_id)
        if source and target:
            for emb in source.embeddings:
                target.add(emb, self.max_samples_per_speaker)

    def identify(self, embedding: np.ndarray, allow_new_speaker: bool = True) -> str | None:
        """Match an embedding against all known speakers' sample buffers
        using best-of-any-sample cosine similarity; mint a new speaker_id
        if nothing matches above the threshold.

        If `allow_new_speaker` is False and nothing matches, returns None
        (caller should treat this as "unknown speaker") instead of
        fabricating a new identity from what is likely a noisy, too-short
        embedding -- see conversation history: short utterances ("Yeah.",
        "Thank you.") produce unreliable embeddings regardless of when
        they're queried, and were observed spawning spurious new speakers
        rather than correctly matching an existing one or being left
        unidentified.
        """
        best_id: str | None = None
        best_score = -1.0
        for speaker_id, profile in self._speakers.items():
            for sample in profile.embeddings:
                score = _cosine_similarity(embedding, sample)
                if score > best_score:
                    best_score = score
                    best_id = speaker_id

        if best_id is not None and best_score >= self.similarity_threshold:
            resolved = self.resolve(best_id)
            self._speakers[resolved].add(embedding, self.max_samples_per_speaker)
            return resolved

        if not allow_new_speaker:
            return None

        new_id = f"speaker_{next(self._id_counter)}"
        self._speakers[new_id] = SpeakerProfile(new_id)
        self._speakers[new_id].add(embedding, self.max_samples_per_speaker)
        return new_id

    def reset(self) -> None:
        self._speakers.clear()
        self._merge_map.clear()
        self._id_counter = itertools.count(1)
