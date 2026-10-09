"""Deepgram real-time streaming backend.

Connects directly to Deepgram's live-streaming WebSocket API (rather than
depending on SDK internals) and acts as a thin relay: incoming PCM16
chunks are forwarded as binary WS frames, and Deepgram's JSON responses
are translated into TranscriptEvents. This is the lowest-latency online
option since Deepgram streams interim results continuously rather than
waiting on fixed-size chunks.

API reference: https://developers.deepgram.com/docs/getting-started-with-live-streaming-audio
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets

from .base import TranscriberBackend, TranscriptEvent

logger = logging.getLogger("stt.deepgram_backend")

DEEPGRAM_WS_BASE = "wss://api.deepgram.com/v1/listen"


class DeepgramBackend(TranscriberBackend):
    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        language: str = "en-US",
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY is required for the deepgram backend")
        self._api_key = api_key
        self._model = model
        self._language = language
        self._sample_rate = sample_rate
        self._channels = channels
        self._queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self._closed = False
        self._ws: websockets.ClientConnection | None = None
        self._connect_task = asyncio.create_task(self._connect_and_listen())

    def _url(self) -> str:
        params = (
            f"model={self._model}&language={self._language}"
            f"&encoding=linear16&sample_rate={self._sample_rate}"
            f"&channels={self._channels}&punctuate=true"
            f"&interim_results=true&endpointing=700"
        )
        return f"{DEEPGRAM_WS_BASE}?{params}"

    async def _connect_and_listen(self) -> None:
        try:
            async with websockets.connect(
                self._url(),
                additional_headers={"Authorization": f"Token {self._api_key}"},
            ) as ws:
                self._ws = ws
                async for raw in ws:
                    if self._closed:
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    alt = (msg.get("channel") or {}).get("alternatives") or []
                    if not alt:
                        continue
                    text = (alt[0].get("transcript") or "").strip()
                    if not text:
                        continue
                    is_final = bool(msg.get("is_final"))
                    event_type = "final" if is_final else "partial"
                    await self._queue.put(TranscriptEvent(type=event_type, text=text))
        except Exception:
            if not self._closed:
                logger.exception("Deepgram connection failed")

    async def feed(self, pcm_chunk: bytes) -> None:
        if self._closed or self._ws is None:
            return
        try:
            await self._ws.send(pcm_chunk)
        except Exception:
            logger.exception("failed to send audio to Deepgram")

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while not self._closed:
            event = await self._queue.get()
            yield event

    async def close(self) -> None:
        self._closed = True
        self._connect_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
