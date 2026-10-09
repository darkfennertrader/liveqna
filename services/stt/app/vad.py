"""VAD-gated speech segmentation.

Wraps `webrtcvad` to turn a stream of fixed-size PCM16 frames into speech
segments: a segment "opens" once enough consecutive frames are classified
as speech, stays open while speech continues, and "closes" (emits as
final) once enough consecutive silent frames follow. While open, the
accumulated audio-so-far is exposed so the caller can re-decode it for a
live-updating partial transcript.

webrtcvad requires frames of exactly 10/20/30ms at 8k/16k/32k/48kHz,
16-bit mono PCM -- this module enforces 20ms frames at the configured
sample rate.
"""

from __future__ import annotations

from dataclasses import dataclass

import webrtcvad


@dataclass
class SegmentUpdate:
    audio: bytes  # full accumulated PCM16 for the currently open segment
    is_final: bool
    segment_id: int  # stable id for this segment, same across all its partial updates


class SpeechSegmenter:
    def __init__(
        self,
        sample_rate: int = 16000,
        aggressiveness: int = 2,
        frame_ms: int = 20,
        silence_duration_ms: int = 700,
        min_speech_ms: int = 250,
        max_segment_ms: int = 12000,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_bytes = int(sample_rate * (frame_ms / 1000.0)) * 2  # s16 mono
        self._vad = webrtcvad.Vad(aggressiveness)
        self._silence_frames_to_close = max(1, silence_duration_ms // frame_ms)
        self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        self._max_speech_frames = max(1, max_segment_ms // frame_ms)

        self._buffer = bytearray()
        self._segment = bytearray()
        self._in_speech = False
        self._speech_frame_count = 0
        self._silence_run = 0
        self._segment_id_counter = 0
        self._current_segment_id = 0

    def set_params(self, aggressiveness: int | None = None,
                    silence_duration_ms: int | None = None,
                    min_speech_ms: int | None = None,
                    max_segment_ms: int | None = None, frame_ms: int = 20) -> None:
        if aggressiveness is not None:
            self._vad = webrtcvad.Vad(aggressiveness)
        if silence_duration_ms is not None:
            self._silence_frames_to_close = max(1, silence_duration_ms // frame_ms)
        if min_speech_ms is not None:
            self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        if max_segment_ms is not None:
            self._max_speech_frames = max(1, max_segment_ms // frame_ms)

    def push(self, pcm_chunk: bytes) -> list[SegmentUpdate]:
        """Feed raw PCM16 bytes (any chunk size); returns zero or more
        SegmentUpdate events produced by the frames contained in this
        chunk."""
        self._buffer.extend(pcm_chunk)
        updates: list[SegmentUpdate] = []

        while len(self._buffer) >= self.frame_bytes:
            frame = bytes(self._buffer[: self.frame_bytes])
            del self._buffer[: self.frame_bytes]

            is_speech = self._vad.is_speech(frame, self.sample_rate)

            if is_speech:
                self._segment.extend(frame)
                self._speech_frame_count += 1
                self._silence_run = 0
                if not self._in_speech and self._speech_frame_count >= self._min_speech_frames:
                    self._in_speech = True
                    self._segment_id_counter += 1
                    self._current_segment_id = self._segment_id_counter
                if self._in_speech:
                    if self._speech_frame_count >= self._max_speech_frames:
                        # Force a checkpoint so continuous speech with no
                        # silence gap still produces committed caption
                        # lines instead of one unbounded, ever-rewritten
                        # blob -- and bounds per-decode latency.
                        updates.append(SegmentUpdate(
                            audio=bytes(self._segment), is_final=True,
                            segment_id=self._current_segment_id,
                        ))
                        self._segment = bytearray()
                        self._speech_frame_count = 0
                        self._silence_run = 0
                        # stay "in speech" -- no need to re-satisfy min_speech_ms,
                        # but this is a fresh segment_id for the caller.
                        self._segment_id_counter += 1
                        self._current_segment_id = self._segment_id_counter
                    else:
                        updates.append(SegmentUpdate(
                            audio=bytes(self._segment), is_final=False,
                            segment_id=self._current_segment_id,
                        ))
            else:
                if self._in_speech:
                    self._segment.extend(frame)  # keep trailing silence for natural decode
                    self._silence_run += 1
                    if self._silence_run >= self._silence_frames_to_close:
                        updates.append(SegmentUpdate(
                            audio=bytes(self._segment), is_final=True,
                            segment_id=self._current_segment_id,
                        ))
                        self._segment = bytearray()
                        self._in_speech = False
                        self._speech_frame_count = 0
                        self._silence_run = 0
                else:
                    self._segment.clear()
                    self._speech_frame_count = 0

        return updates
