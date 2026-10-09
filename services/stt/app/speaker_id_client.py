"""Thin client for the speaker-id service's /ws/segment endpoint.

Maintains a single persistent WebSocket connection (reconnecting on drop)
and exposes a simple request/response call per segment. Used by
LocalWhisperBackend to resolve a speaker_id + overlap flag for each VAD
segment in parallel with the Whisper decode.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging

import websockets

logger = logging.getLogger("stt.speaker_id_client")


class SpeakerIdClient:
    def __init__(self, ws_url: str) -> None:
        self._ws_url = f"{ws_url}/ws/segment"
        self._ws: websockets.ClientConnection | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _ensure_connected(self) -> websockets.ClientConnection:
        if self._ws is not None:
            return self._ws
        self._ws = await websockets.connect(self._ws_url, max_size=None)
        return self._ws

    async def identify(self, segment_id: int, pcm16_mono: bytes, timeout: float = 5.0) -> dict:
        """Returns {"speaker_id": str|None, "is_overlap": bool}. On any
        failure (service down, timeout, reconnect needed), returns a
        neutral result rather than raising -- speaker tagging is a
        best-effort enhancement and must never block transcription."""
        if self._closed:
            return {"speaker_id": None, "is_overlap": False}

        async with self._lock:
            try:
                ws = await self._ensure_connected()
                payload = json.dumps({
                    "segment_id": segment_id,
                    "audio": base64.b64encode(pcm16_mono).decode("ascii"),
                })
                await asyncio.wait_for(ws.send(payload), timeout=timeout)
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                resp = json.loads(raw)
                if "error" in resp:
                    logger.warning("speaker-id returned error: %s", resp["error"])
                    return {"speaker_id": None, "is_overlap": False}
                return {
                    "speaker_id": resp.get("speaker_id"),
                    "is_overlap": bool(resp.get("is_overlap", False)),
                }
            except Exception:
                logger.warning("speaker-id request failed, resetting connection", exc_info=True)
                self._ws = None
                return {"speaker_id": None, "is_overlap": False}

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
