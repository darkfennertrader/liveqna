"""stt service -- loads/swaps a transcription backend and streams live
transcript events.

Endpoints:
  GET  /health
  POST /configure           -- swap/configure the active backend
  WS   /ws/audio            -- capture service pushes raw PCM16 frames here
  WS   /ws/transcript        -- ui subscribes here for partial/final events
"""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .backends.base import TranscriberBackend
from .backends.local_whisper import LocalWhisperBackend
from .backends.openai_backend import OpenAIBackend
from .backends.deepgram_backend import DeepgramBackend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stt.main")

app = FastAPI(title="liveqna-stt")


class SttState:
    def __init__(self) -> None:
        self.backend: TranscriberBackend | None = None
        self.backend_name: str | None = None
        self.subscribers: set[WebSocket] = set()
        self.broadcast_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    async def broadcast_loop(self, backend: TranscriberBackend) -> None:
        try:
            async for event in backend.events():
                payload = event.to_dict()
                dead = []
                for ws in list(self.subscribers):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.subscribers.discard(ws)
        except asyncio.CancelledError:
            pass


state = SttState()


def _build_backend(cfg: dict) -> TranscriberBackend:
    backend_name = cfg.get("backend", "local")
    sample_rate = int(cfg.get("sample_rate", 16000))

    if backend_name == "local":
        return LocalWhisperBackend(
            model_size=cfg.get("model_size", "small"),
            device=cfg.get("device", "cuda"),
            compute_type=cfg.get("compute_type", "int8_float32"),
            language=cfg.get("language") or None,
            download_root=os.environ.get("HF_HOME"),
            sample_rate=sample_rate,
            vad_aggressiveness=int(cfg.get("vad_aggressiveness", 2)),
            silence_duration_ms=int(cfg.get("silence_duration_ms", 700)),
            min_speech_ms=int(cfg.get("min_speech_ms", 250)),
            max_segment_ms=int(cfg.get("max_segment_ms", 12000)),
        )

    if backend_name == "openai":
        return OpenAIBackend(
            api_key=cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            model=cfg.get("model") or os.environ.get("OPENAI_STT_MODEL", "gpt-4o-transcribe"),
            sample_rate=sample_rate,
            chunk_ms=int(cfg.get("chunk_ms", 4000)),
            language=cfg.get("language") or None,
        )
    if backend_name == "deepgram":
        return DeepgramBackend(
            api_key=cfg.get("api_key") or os.environ.get("DEEPGRAM_API_KEY", ""),
            model=cfg.get("model") or os.environ.get("DEEPGRAM_MODEL", "nova-3"),
            language=cfg.get("language") or os.environ.get("DEEPGRAM_LANGUAGE", "en-US"),
            sample_rate=sample_rate,
        )
    raise ValueError(f"unknown backend: {backend_name}")


class ConfigureRequest(BaseModel):
    backend: str = "local"
    model_size: str | None = None
    device: str | None = None
    compute_type: str | None = None
    language: str | None = None
    sample_rate: int | None = None
    vad_aggressiveness: int | None = None
    silence_duration_ms: int | None = None
    min_speech_ms: int | None = None
    max_segment_ms: int | None = None
    api_key: str | None = None
    model: str | None = None
    chunk_ms: int | None = None


def _default_config() -> dict:
    return {
        "backend": os.environ.get("STT_BACKEND", "local"),
        "model_size": os.environ.get("STT_MODEL_SIZE", "small"),
        "device": os.environ.get("STT_DEVICE", "cuda"),
        "compute_type": os.environ.get("STT_COMPUTE_TYPE", "int8_float32"),
        "language": os.environ.get("STT_LANGUAGE", "en"),
        "sample_rate": int(os.environ.get("CAPTURE_SAMPLE_RATE", "16000")),
        "vad_aggressiveness": int(os.environ.get("VAD_AGGRESSIVENESS", "2")),
        "silence_duration_ms": int(os.environ.get("SILENCE_DURATION_MS", "700")),
        "min_speech_ms": int(os.environ.get("MIN_SPEECH_MS", "250")),
        "max_segment_ms": int(os.environ.get("MAX_SEGMENT_MS", "12000")),
    }



@app.get("/health")
async def health():
    return {"status": "ok", "backend": state.backend_name}


@app.post("/configure")
async def configure(req: ConfigureRequest):
    cfg = req.model_dump(exclude_none=True)
    merged = {**_default_config(), **cfg}

    async with state.lock:
        if state.broadcast_task is not None:
            state.broadcast_task.cancel()
            state.broadcast_task = None
        if state.backend is not None:
            await state.backend.close()
            state.backend = None

        try:
            if merged.get("backend") == "local":
                backend = await asyncio.to_thread(_build_backend, merged)
            else:
                backend = _build_backend(merged)
        except Exception as exc:
            logger.exception("failed to build backend")
            return JSONResponse({"error": str(exc)}, status_code=400)

        state.backend = backend
        state.backend_name = merged.get("backend")
        state.broadcast_task = asyncio.create_task(state.broadcast_loop(backend))

    return {"status": "configured", "backend": state.backend_name}


@app.on_event("startup")
async def _startup() -> None:
    # Load the default backend (per .env) eagerly so the first "start" from
    # the UI doesn't pay model-load latency.
    try:
        await configure(ConfigureRequest(**_default_config()))
    except Exception:
        logger.exception("failed to initialize default backend")


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            chunk = await ws.receive_bytes()
            if state.backend is not None:
                await state.backend.feed(chunk)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/transcript")
async def ws_transcript(ws: WebSocket) -> None:
    await ws.accept()
    state.subscribers.add(ws)
    try:
        while True:
            # keep the connection open; this socket is emit-only
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.subscribers.discard(ws)

