"""Local GPU-resident transcription backend using faster-whisper
(CTranslate2). The model is loaded once, lazily, on first use and stays
resident until `close()` is called (e.g. when the UI switches to an
online backend), so switching away frees the VRAM.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np
from faster_whisper import WhisperModel

from .base import TranscriberBackend, TranscriptEvent
from ..vad import SpeechSegmenter

logger = logging.getLogger("stt.local_whisper")


def _pcm16_bytes_to_float32(pcm: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    return audio


class LocalWhisperBackend(TranscriberBackend):
    def __init__(
        self,
        model_size: str = "small",
        device: str = "cuda",
        compute_type: str = "int8_float32",
        language: str | None = "en",
        download_root: str | None = None,
        sample_rate: int = 16000,
        vad_aggressiveness: int = 2,
        silence_duration_ms: int = 700,
        min_speech_ms: int = 250,
        max_segment_ms: int = 12000,
    ) -> None:
        self.language = language
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self._segmenter = SpeechSegmenter(
            sample_rate=sample_rate,
            aggressiveness=vad_aggressiveness,
            silence_duration_ms=silence_duration_ms,
            min_speech_ms=min_speech_ms,
            max_segment_ms=max_segment_ms,
        )
        self._closed = False
        self._decode_lock = asyncio.Lock()

        # Single-flight coalescing for partial decodes: at most one partial
        # decode runs at a time. New partial audio overwrites the pending
        # slot instead of spawning another task, so the pipeline always
        # targets the freshest audio and can never build an ever-growing
        # backlog of stale decode tasks. Finals bypass this slot entirely
        # (they are infrequent and must never be dropped).
        self._pending_partial: bytes | None = None
        self._partial_inflight = False

        logger.info(
            "loading faster-whisper model=%s device=%s compute_type=%s",
            model_size, device, compute_type,
        )
        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=download_root,
        )
        logger.info("faster-whisper model loaded")

    async def feed(self, pcm_chunk: bytes) -> None:
        if self._closed:
            return
        updates = self._segmenter.push(pcm_chunk)
        for update in updates:
            if update.is_final:
                asyncio.create_task(self._decode_final(update.audio, update.segment_id))
            else:
                self._pending_partial = (update.audio, update.segment_id)
                if not self._partial_inflight:
                    self._partial_inflight = True
                    asyncio.create_task(self._drain_partial())

    async def _drain_partial(self) -> None:
        try:
            while self._pending_partial is not None:
                audio_bytes, segment_id = self._pending_partial
                self._pending_partial = None
                await self._decode(audio_bytes, is_final=False, segment_id=segment_id)
        finally:
            self._partial_inflight = False

    async def _decode_final(self, audio_bytes: bytes, segment_id: int) -> None:
        await self._decode(audio_bytes, is_final=True, segment_id=segment_id)

    async def _decode(self, audio_bytes: bytes, is_final: bool, segment_id: int) -> None:
        async with self._decode_lock:
            if self._closed:
                return
            audio = _pcm16_bytes_to_float32(audio_bytes)
            try:
                text = await asyncio.to_thread(self._transcribe_sync, audio)
            except Exception:
                logger.exception("transcription failed")
                return
            if not text:
                return
            event_type = "final" if is_final else "partial"
            await self._queue.put(TranscriptEvent(
                type=event_type,
                text=text,
                segment_id=segment_id,
            ))


    def _transcribe_sync(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=False,  # segmentation already handled by SpeechSegmenter
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while not self._closed:
            event = await self._queue.get()
            yield event

    async def close(self) -> None:
        self._closed = True
        self._pending_partial = None
        # Drop the model reference and release CUDA memory held by
        # ctranslate2 so switching to an online backend frees VRAM.
        # Wait for an in-flight decode before deleting the model it uses.
        async with self._decode_lock:
            if hasattr(self, "_model"):
                del self._model
        import gc

        gc.collect()

