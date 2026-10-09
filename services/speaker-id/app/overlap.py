"""Overlap detection using pyannote/segmentation-3.0.

The gated OverlappedSpeechDetection *pipeline* wrapper from older
pyannote.audio releases was removed in pyannote.audio 4.x, so this module
runs the underlying Powerset segmentation model directly and decodes its
multi-label output itself: a frame with 2+ simultaneously active speaker
classes is overlapping speech.

The model expects exactly 10s of mono 16kHz audio per the model card;
shorter segments are zero-padded, longer ones are truncated to the first
10s (sufficient for a per-utterance overlap flag -- this is deliberately
not a full-recording diarization pass).

Two guards against false positives, both verified against real recorded
audio (see conversation history):

1. Segments shorter than `min_clip_ms` skip detection entirely and are
   reported as non-overlapping. Tiling a very short clip to fill the
   model's fixed 10s window requires many repeats, and that repetition
   was observed to make the model misread a single speaker as two --
   real audio measured as high as 100% of frames falsely flagged for a
   genuinely single-speaker ~1.5-1.8s clip. Overlapping speech lasting
   under ~2s is also not reliably callable either way, so skipping
   detection below this floor loses little.
2. The decision requires a contiguous run of 2+-speaker frames lasting
   at least `min_run_ms`, not just any single flagged frame. IMPORTANT:
   a systematic scan of real meeting audio (160 windows across a full
   recording) found that this false "2+ speakers" artifact is not
   confined to brief blips -- pitch-tracking and spectral-flatness
   analysis confirmed single, continuous, clearly one-voice speech
   (smooth F0 contour through normal voiced/unvoiced consonant
   transitions, low spectral flatness) producing flagged runs as long
   as ~2000ms on this model. No acoustically-confirmed genuine-overlap
   example was found in that scan to calibrate against. `min_run_ms`
   therefore defaults high (above every false positive measured) as a
   deliberately conservative choice, not a scientifically validated
   true/false-overlap boundary -- it will likely miss some genuinely
   short real overlaps. If this proves too conservative in practice,
   the real fix is a different/better overlap model, not a smaller
   threshold.
"""

from __future__ import annotations

import logging

import numpy as np
import torch
from pyannote.audio import Model
from pyannote.audio.utils.powerset import Powerset

logger = logging.getLogger("speaker_id.overlap")

MODEL_SAMPLE_RATE = 16000
MODEL_WINDOW_SECONDS = 10
MODEL_WINDOW_SAMPLES = MODEL_SAMPLE_RATE * MODEL_WINDOW_SECONDS


class OverlapDetector:
    def __init__(
        self,
        hf_token: str,
        device: str = "cuda",
        download_root: str | None = None,
        min_clip_ms: int = 2000,
        min_run_ms: int = 2500,
    ) -> None:
        logger.info("loading pyannote/segmentation-3.0")
        kwargs = {"token": hf_token}
        if download_root:
            kwargs["cache_dir"] = download_root
        self._model = Model.from_pretrained("pyannote/segmentation-3.0", **kwargs)
        self._device = torch.device(device)
        self._model.to(self._device)
        self._model.eval()
        self._powerset = Powerset(num_classes=3, max_set_size=2)
        self._min_clip_samples = int(MODEL_SAMPLE_RATE * (min_clip_ms / 1000.0))
        self._min_run_ms = min_run_ms
        logger.info(
            "pyannote/segmentation-3.0 loaded on %s (min_clip_ms=%d, min_run_ms=%d)",
            device, min_clip_ms, min_run_ms,
        )

    def is_overlapping(self, pcm16_mono: bytes) -> bool:
        """Return True if 2+ simultaneously active speakers are detected
        anywhere in the segment's real (non-padded) audio.

        The model expects a fixed 10s window. Zero-padding a short segment
        creates an abrupt speech-to-silence cliff that was observed to
        trigger spurious "two speakers" activations on genuinely
        single-speaker audio (verified against real speech). Tiling the
        short segment to fill the window instead avoids that artificial
        edge, and only the frames covering the original (non-tiled) audio
        are inspected for the overlap decision.
        """
        audio = np.frombuffer(pcm16_mono, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return False

        # Guard 1: below this length, tiling has to repeat the clip so
        # many times that the repetition artifact itself was measured to
        # dominate the model's output -- skip detection rather than trust
        # a result known to be unreliable at this length.
        if audio.size < self._min_clip_samples:
            return False

        original_len = min(audio.size, MODEL_WINDOW_SAMPLES)
        if audio.size < MODEL_WINDOW_SAMPLES:
            repeats = -(-MODEL_WINDOW_SAMPLES // audio.size)  # ceil div
            tiled = np.tile(audio, repeats)[:MODEL_WINDOW_SAMPLES]
            audio = tiled
        else:
            audio = audio[:MODEL_WINDOW_SAMPLES]

        waveform = torch.from_numpy(audio).unsqueeze(0).unsqueeze(0).to(self._device)
        with torch.no_grad():
            logits = self._model(waveform)
        multilabel = self._powerset.to_multilabel(logits.cpu())
        active_count = multilabel.sum(dim=-1).squeeze(0)

        # Only consider frames within the original (non-tiled) audio span.
        num_frames = active_count.shape[0]
        frames_per_sample = num_frames / MODEL_WINDOW_SAMPLES
        valid_frames = max(1, int(original_len * frames_per_sample))

        # Guard 2: require a contiguous run of 2+-speaker frames lasting
        # at least `min_run_ms`, not just any single flagged frame. See
        # the module docstring: this threshold is set conservatively
        # above every false-positive run length measured on real audio
        # (up to ~2000ms), not a validated true/false-overlap boundary.
        is_multi = (active_count[:valid_frames] >= 2).numpy()
        min_run_frames = max(1, int(self._min_run_ms / 1000.0 * frames_per_sample * MODEL_SAMPLE_RATE))
        longest_run = 0
        current_run = 0
        for flagged in is_multi:
            if flagged:
                current_run += 1
                longest_run = max(longest_run, current_run)
            else:
                current_run = 0
        return longest_run >= min_run_frames
