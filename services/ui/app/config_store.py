"""Holds the UI's view of "current effective config", seeded from the
shared .env at startup. The UI is the single source of truth for the
full config shape; `capture` and `stt` only need to understand their own
`/configure` payload subset.
"""

from __future__ import annotations

import os


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def initial_config() -> dict:
    return {
        "capture": {
            "source_node": os.environ.get("CAPTURE_SOURCE_NODE") or "",
            "source_kind": "source",
            "sample_rate": int(os.environ.get("CAPTURE_SAMPLE_RATE", "16000")),
            "channels": int(os.environ.get("CAPTURE_CHANNELS", "1")),
        },
        "model": {
            "backend": os.environ.get("STT_BACKEND", "local"),
            "model_size": os.environ.get("STT_MODEL_SIZE", "small"),
            "device": os.environ.get("STT_DEVICE", "cuda"),
            "compute_type": os.environ.get("STT_COMPUTE_TYPE", "int8_float32"),
            "language": os.environ.get("STT_LANGUAGE", "en"),
            "vad_aggressiveness": int(os.environ.get("VAD_AGGRESSIVENESS", "2")),
            "silence_duration_ms": int(os.environ.get("SILENCE_DURATION_MS", "700")),
            "min_speech_ms": int(os.environ.get("MIN_SPEECH_MS", "250")),
            "max_segment_ms": int(os.environ.get("MAX_SEGMENT_MS", "12000")),
            "api_key": "",
            "model": "",
            "chunk_ms": int(os.environ.get("OPENAI_CHUNK_MS", "4000")),
        },
        "speaker": {
            "embedding_backend": os.environ.get("EMBEDDING_BACKEND", "ecapa"),
            "similarity_threshold": float(os.environ.get("SPEAKER_SIMILARITY_THRESHOLD", "0.40")),
            "overlap_detection_enabled": _bool(os.environ.get("OVERLAP_DETECTION_ENABLED"), True),
        },
    }


class ConfigStore:
    def __init__(self) -> None:
        self.state = initial_config()

    def update_capture(self, patch: dict) -> dict:
        self.state["capture"].update({k: v for k, v in patch.items() if v is not None})
        return self.state["capture"]

    def update_model(self, patch: dict) -> dict:
        self.state["model"].update({k: v for k, v in patch.items() if v is not None})
        return self.state["model"]

    def update_speaker(self, patch: dict) -> dict:
        self.state["speaker"].update({k: v for k, v in patch.items() if v is not None})
        return self.state["speaker"]
