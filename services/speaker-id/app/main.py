"""speaker-id service -- identifies which known speaker an utterance
belongs to (via a swappable embedding backend + online clustering) and
flags overlapping speech (via pyannote/segmentation-3.0).

Endpoints:
  GET  /health
  POST /configure         -- live-tunable similarity threshold, embedding
                              backend swap, etc.
  POST /reset              -- clear session speaker state (new capture)
  POST /merge              -- manual merge: fold source speaker into target
  WS   /ws/segment         -- stt sends {audio: base64 pcm16} per VAD segment,
                               receives {speaker_id, is_overlap} back
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .clustering import SpeakerClusterer
from .embeddings.base import EmbeddingBackend
from .embeddings.ecapa_backend import EcapaBackend
from .embeddings.resemblyzer_backend import ResemblyzerBackend
from .enrollment_store import EnrollmentStore
from .overlap import OverlapDetector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("speaker_id.main")

app = FastAPI(title="liveqna-speaker-id")

HF_TOKEN = os.environ.get("PYANNOTE_HF_TOKEN", "")
DOWNLOAD_ROOT = os.environ.get("HF_HOME")
DEVICE = os.environ.get("SPEAKER_ID_DEVICE", "cuda")
OVERLAP_ENABLED = os.environ.get("OVERLAP_DETECTION_ENABLED", "true").lower() == "true"
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
# Enrolled-profile matches use a higher bar than the auto-cluster
# threshold by default, since enrollment is meant to be a confident,
# low-false-positive path -- tunable independently via /configure.
ENROLLMENT_SIMILARITY_THRESHOLD = float(os.environ.get("ENROLLMENT_SIMILARITY_THRESHOLD", "0.5"))
# Overlap detection on very short tiled clips was measured to produce
# false positives (see overlap.py docstring for the real-audio numbers);
# these two guards are tunable independently of the other speaker-id
# thresholds via the same env-var convention.
OVERLAP_MIN_CLIP_MS = int(os.environ.get("OVERLAP_MIN_CLIP_MS", "2000"))
OVERLAP_MIN_RUN_MS = int(os.environ.get("OVERLAP_MIN_RUN_MS", "2500"))


def _build_embedding_backend(name: str) -> EmbeddingBackend:
    if name == "ecapa":
        return EcapaBackend(device=DEVICE, download_root=DOWNLOAD_ROOT)
    if name == "resemblyzer":
        return ResemblyzerBackend(device=DEVICE)
    raise ValueError(f"unknown embedding backend: {name}")


class SpeakerIdState:
    def __init__(self) -> None:
        self.embedding_backend_name: str | None = None
        self.embedder: EmbeddingBackend | None = None
        self.clusterer: SpeakerClusterer | None = None
        self.overlap_detector: OverlapDetector | None = None
        self.lock = asyncio.Lock()
        # Minimum audio duration (ms) required before a *new* speaker_id
        # may be minted. Below this, an utterance can still match an
        # existing speaker (matching against a good reference is low-risk
        # even from a short clip) but if nothing matches, the result is
        # "unknown" rather than a fabricated new identity -- see
        # conversation history: short utterances ("Yeah.", "Thank you.")
        # produce unreliable embeddings and were observed spawning
        # spurious new speakers.
        self.min_new_speaker_ms = int(os.environ.get("SPEAKER_MIN_NEW_SPEAKER_MS", "1500"))
        self.sample_rate = int(os.environ.get("SPEAKER_ID_SAMPLE_RATE", "16000"))
        self.enrollment_store: EnrollmentStore | None = None
        self.enrollment_similarity_threshold = ENROLLMENT_SIMILARITY_THRESHOLD


state = SpeakerIdState()


@app.on_event("startup")
async def _startup() -> None:
    state.enrollment_store = EnrollmentStore(QDRANT_URL)

    # Deliberately do NOT apply SPEAKER_SIMILARITY_THRESHOLD here even if
    # set in .env -- that value may have been calibrated for a different
    # backend than the one selected by EMBEDDING_BACKEND, and each
    # backend's own default_similarity_threshold is the only threshold
    # calibrated against real audio for its specific similarity scale.
    # Use /configure after startup to explicitly override.
    default_backend = os.environ.get("EMBEDDING_BACKEND", "ecapa")
    await _swap_embedding_backend(default_backend)

    if OVERLAP_ENABLED:
        if not HF_TOKEN:
            logger.warning("OVERLAP_DETECTION_ENABLED but no PYANNOTE_HF_TOKEN set; overlap detection disabled")
        else:
            state.overlap_detector = OverlapDetector(
                hf_token=HF_TOKEN,
                device=DEVICE,
                download_root=DOWNLOAD_ROOT,
                min_clip_ms=OVERLAP_MIN_CLIP_MS,
                min_run_ms=OVERLAP_MIN_RUN_MS,
            )


async def _swap_embedding_backend(name: str, override_threshold: str | None = None) -> None:
    """Load a new embedding backend and reset clustering state. Switching
    backends changes the embedding space entirely (different dimension
    and similarity scale -- see embeddings/*_backend.py docstrings), so
    any previously-clustered speakers from the old backend are no longer
    comparable and must be discarded."""
    async with state.lock:
        if state.embedder is not None:
            state.embedder.close()
        state.embedder = await asyncio.to_thread(_build_embedding_backend, name)
        state.embedding_backend_name = name

        threshold = (
            float(override_threshold)
            if override_threshold is not None
            else state.embedder.default_similarity_threshold
        )
        state.clusterer = SpeakerClusterer(similarity_threshold=threshold)
        logger.info(
            "embedding backend set to %s (similarity_threshold=%.3f)",
            name, threshold,
        )



class ConfigureRequest(BaseModel):
    similarity_threshold: float | None = None
    max_samples_per_speaker: int | None = None
    embedding_backend: str | None = None
    min_new_speaker_ms: int | None = None
    enrollment_similarity_threshold: float | None = None


class MergeRequest(BaseModel):
    source_id: str
    target_id: str


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "overlap_detection": state.overlap_detector is not None,
        "embedding_backend": state.embedding_backend_name,
        "similarity_threshold": state.clusterer.similarity_threshold if state.clusterer else None,
        "min_new_speaker_ms": state.min_new_speaker_ms,
        "enrollment_similarity_threshold": state.enrollment_similarity_threshold,
    }


@app.post("/configure")
async def configure(req: ConfigureRequest):
    if req.min_new_speaker_ms is not None:
        state.min_new_speaker_ms = req.min_new_speaker_ms
    if req.enrollment_similarity_threshold is not None:
        state.enrollment_similarity_threshold = req.enrollment_similarity_threshold
    if req.embedding_backend is not None and req.embedding_backend != state.embedding_backend_name:
        try:
            await _swap_embedding_backend(req.embedding_backend, override_threshold=(
                str(req.similarity_threshold) if req.similarity_threshold is not None else None
            ))
        except Exception as exc:
            logger.exception("failed to swap embedding backend")
            return JSONResponse({"error": str(exc)}, status_code=400)
    elif state.clusterer is not None:
        state.clusterer.set_params(
            similarity_threshold=req.similarity_threshold,
            max_samples_per_speaker=req.max_samples_per_speaker,
        )

    return {
        "status": "configured",
        "embedding_backend": state.embedding_backend_name,
        "similarity_threshold": state.clusterer.similarity_threshold if state.clusterer else None,
    }


@app.post("/reset")
async def reset():
    if state.clusterer is not None:
        state.clusterer.reset()
    return {"status": "reset"}


@app.post("/merge")
async def merge(req: MergeRequest):
    if state.clusterer is None:
        return JSONResponse({"error": "clusterer not ready"}, status_code=400)
    state.clusterer.merge(req.source_id, req.target_id)
    return {"status": "merged", "source_id": req.source_id, "target_id": state.clusterer.resolve(req.source_id)}


class EnrollRequest(BaseModel):
    name: str
    audio: str  # base64 pcm16 mono @ state.sample_rate
    source_label: str = ""
    start_sec: float | None = None
    end_sec: float | None = None


@app.post("/enroll")
async def enroll(req: EnrollRequest):
    if state.embedder is None or state.enrollment_store is None:
        return JSONResponse({"error": "embedder not ready"}, status_code=400)
    try:
        pcm = base64.b64decode(req.audio)
    except Exception:
        return JSONResponse({"error": "invalid audio payload"}, status_code=400)

    min_clip_seconds = float(os.environ.get("ENROLLMENT_MIN_CLIP_SECONDS", "5"))
    audio_seconds = (len(pcm) / 2) / state.sample_rate
    if audio_seconds < min_clip_seconds:
        return JSONResponse({
            "error": f"clip too short for a reliable reference ({audio_seconds:.1f}s < {min_clip_seconds:.1f}s minimum)",
        }, status_code=400)

    try:
        embedding = state.embedder.embed(pcm)
        state.enrollment_store.enroll(
            req.name, embedding, state.embedding_backend_name,
            source_label=req.source_label, start_sec=req.start_sec, end_sec=req.end_sec,
        )
    except Exception as exc:
        logger.exception("enrollment failed")
        return JSONResponse({"error": str(exc)}, status_code=400)

    return {"status": "enrolled", "name": req.name, "embedding_backend": state.embedding_backend_name}


@app.get("/enrolled")
async def list_enrolled():
    if state.enrollment_store is None:
        return {"speakers": []}
    return {"speakers": state.enrollment_store.list_enrolled(state.embedding_backend_name)}


@app.delete("/enroll/{name}")
async def delete_enrolled(name: str):
    if state.enrollment_store is None:
        return JSONResponse({"error": "store not ready"}, status_code=400)
    removed = state.enrollment_store.remove(name, state.embedding_backend_name)
    return {"status": "removed" if removed else "not_found", "name": name}


@app.websocket("/ws/segment")
async def ws_segment(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            audio_b64 = msg.get("audio", "")
            segment_id = msg.get("segment_id")
            try:
                pcm = base64.b64decode(audio_b64)
            except Exception:
                await ws.send_json({"segment_id": segment_id, "error": "invalid audio payload"})
                continue

            if state.embedder is None or state.clusterer is None:
                await ws.send_json({"segment_id": segment_id, "error": "embedder not ready"})
                continue

            try:
                audio_ms = (len(pcm) / 2) / state.sample_rate * 1000.0
                embedding = state.embedder.embed(pcm)

                # Enrolled (named, persisted) profiles are checked first --
                # they're a trusted reference, so a match is used directly
                # regardless of this segment's own duration. Falls through
                # to the existing anonymous auto-clustering if nothing
                # enrolled matches.
                speaker_id = None
                if state.enrollment_store is not None:
                    match = state.enrollment_store.match(
                        embedding, state.embedding_backend_name,
                        similarity_threshold=state.enrollment_similarity_threshold,
                    )
                    if match is not None:
                        speaker_id = match[0]

                if speaker_id is None:
                    allow_new_speaker = audio_ms >= state.min_new_speaker_ms
                    speaker_id = state.clusterer.identify(embedding, allow_new_speaker=allow_new_speaker)
            except Exception:
                logger.exception("speaker identification failed")
                await ws.send_json({"segment_id": segment_id, "error": "identification failed"})
                continue

            is_overlap = False
            if state.overlap_detector is not None:
                try:
                    is_overlap = state.overlap_detector.is_overlapping(pcm)
                except Exception:
                    logger.exception("overlap detection failed")

            await ws.send_json({
                "segment_id": segment_id,
                "speaker_id": speaker_id,
                "is_overlap": is_overlap,
            })
    except WebSocketDisconnect:
        pass

