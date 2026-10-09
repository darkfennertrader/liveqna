"""OpenAI transcription backend.

OpenAI's transcription endpoint (`client.audio.transcriptions.create`) is
file-based, not a persistent stream, so this backend buffers incoming
PCM16 into fixed-duration chunks (`chunk_ms`, default 4s) and issues one
transcription request per chunk, emitting each result as a "final" event.
This trades a little latency for simplicity/robustness compared to the
local VAD-gated approach; it is NOT the lowest-latency option (see
DeepgramBackend for true streaming) but requires no local GPU.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import TranscriberBackend, TranscriptEvent

logger = logging.getLogger("stt.openai_backend")


class OpenAIBackend(TranscriberBackend):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-transcribe",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 4000,
        language: str | None = "en",
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai backend")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._sample_rate = sample_rate
        self._channels = channels
        self._language = language
        self._chunk_bytes = int(sample_rate * channels * 2 * (chunk_ms / 1000.0))
        self._buffer = bytearray()
        self._queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self._closed = False

    def _wrap_wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(pcm)
        buf.seek(0)
        buf.name = "chunk.wav"  # OpenAI SDK uses this to infer content-type
        return buf

    async def feed(self, pcm_chunk: bytes) -> None:
        if self._closed:
            return
        self._buffer.extend(pcm_chunk)
        if len(self._buffer) >= self._chunk_bytes:
            chunk = bytes(self._buffer[: self._chunk_bytes])
            del self._buffer[: self._chunk_bytes]
            asyncio.create_task(self._transcribe(chunk))

    async def _transcribe(self, pcm: bytes) -> None:
        try:
            wav_file = self._wrap_wav(pcm)
            resp = await self._client.audio.transcriptions.create(
                model=self._model,
                file=wav_file,
                language=self._language,
            )
            text = (resp.text or "").strip()
        except Exception:
            logger.exception("OpenAI transcription request failed")
            return
        if text:
            await self._queue.put(TranscriptEvent(type="final", text=text))

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while not self._closed:
            event = await self._queue.get()
            yield event

    async def close(self) -> None:
        self._closed = True
        await self._client.close()
