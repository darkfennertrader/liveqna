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
from ..speaker_id_client import SpeakerIdClient

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
        speaker_id_ws_url: str | None = None,
        speaker_min_utterance_ms: int = 500,
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

        # speaker-id lookups now run exactly once per segment, on its
        # *final* audio (full utterance), not on an early partial -- see
        # conversation history: querying from the first ~500ms partial
        # produced noisy embeddings that caused both false splits (same
        # speaker mismatched against their own later utterances) and
        # false merges (coincidentally close to a different speaker).
        # The final decode awaits this result (finals are infrequent, so
        # the extra latency is acceptable) rather than caching a stale
        # early guess. Partials simply carry no speaker_id/is_overlap.
        self._speaker_client = SpeakerIdClient(speaker_id_ws_url) if speaker_id_ws_url else None
        self._speaker_min_bytes = int(sample_rate * (speaker_min_utterance_ms / 1000.0)) * 2

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

    async def _get_speaker_info(self, segment_id: int, audio_bytes: bytes) -> dict:
        if self._speaker_client is None or len(audio_bytes) < self._speaker_min_bytes:
            return {}
        return await self._speaker_client.identify(segment_id, audio_bytes)

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
                if is_final:
                    # Run the Whisper decode and the speaker-id lookup
                    # concurrently -- both read the same final audio, and
                    # there's no dependency between them.
                    text, speaker_info = await asyncio.gather(
                        asyncio.to_thread(self._transcribe_sync, audio),
                        self._get_speaker_info(segment_id, audio_bytes),
                    )
                else:
                    text = await asyncio.to_thread(self._transcribe_sync, audio)
                    speaker_info = {}
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
                speaker_id=speaker_info.get("speaker_id"),
                is_overlap=bool(speaker_info.get("is_overlap", False)),
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
        if self._speaker_client is not None:
            await self._speaker_client.close()
        # Drop the model reference and release CUDA memory held by
        # ctranslate2 so switching to an online backend frees VRAM.
        del self._model
        import gc

        gc.collect()
