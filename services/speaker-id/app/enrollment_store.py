"""Qdrant-backed storage for enrolled speaker profiles.

Enrolled profiles are persisted (survive container restarts, usable
across multiple future calls) and keyed by name rather than an anonymous
speaker_id -- confirmed design: "Alice" becomes the speaker_id directly
for every utterance that matches her enrolled reference, no "unknown ->
rename" step needed.

Embeddings from different backends are NOT comparable (see conversation
history: ECAPA-TDNN ~192-dim/0.40 threshold vs Resemblyzer ~256-dim/0.78
threshold) -- each embedding backend gets its own collection, named
`enrolled_speakers__{backend_name}`, so switching backends can never
silently cross-match stale vectors from a different embedding space.
"""

from __future__ import annotations

import logging
import uuid

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger("speaker_id.enrollment_store")


def _collection_name(embedding_backend: str) -> str:
    return f"enrolled_speakers__{embedding_backend}"


def _point_id(name: str) -> str:
    # Deterministic id from the name so re-enrolling the same name
    # overwrites rather than duplicates.
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"liveqna-speaker:{name}"))


class EnrollmentStore:
    def __init__(self, url: str) -> None:
        self._client = QdrantClient(url=url)
        self._ensured_collections: set[str] = set()

    def _ensure_collection(self, embedding_backend: str, vector_size: int) -> str:
        name = _collection_name(embedding_backend)
        if name in self._ensured_collections:
            return name
        if not self._client.collection_exists(name):
            self._client.create_collection(
                name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            logger.info("created Qdrant collection %s (size=%d)", name, vector_size)
        self._ensured_collections.add(name)
        return name

    def enroll(self, name: str, embedding: np.ndarray, embedding_backend: str,
               source_label: str = "", start_sec: float | None = None,
               end_sec: float | None = None) -> None:
        collection = self._ensure_collection(embedding_backend, embedding.shape[0])
        self._client.upsert(
            collection,
            points=[
                PointStruct(
                    id=_point_id(name),
                    vector=embedding.tolist(),
                    payload={
                        "name": name,
                        "embedding_backend": embedding_backend,
                        "source_label": source_label,
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                    },
                )
            ],
        )
        logger.info("enrolled speaker '%s' (backend=%s)", name, embedding_backend)

    def match(self, embedding: np.ndarray, embedding_backend: str,
              similarity_threshold: float) -> tuple[str, float] | None:
        """Return (name, score) for the best-matching enrolled profile
        above `similarity_threshold`, or None if nothing matches well
        enough (or no profiles exist yet for this backend)."""
        collection = _collection_name(embedding_backend)
        if not self._client.collection_exists(collection):
            return None
        results = self._client.query_points(
            collection,
            query=embedding.tolist(),
            limit=1,
        )
        if not results.points:
            return None
        point = results.points[0]
        if point.score < similarity_threshold:
            return None
        return point.payload.get("name"), point.score

    def list_enrolled(self, embedding_backend: str) -> list[dict]:
        collection = _collection_name(embedding_backend)
        if not self._client.collection_exists(collection):
            return []
        points, _ = self._client.scroll(collection, limit=1000, with_payload=True, with_vectors=False)
        return [p.payload for p in points]

    def remove(self, name: str, embedding_backend: str) -> bool:
        collection = _collection_name(embedding_backend)
        if not self._client.collection_exists(collection):
            return False
        self._client.delete(collection, points_selector=[_point_id(name)])
        return True
