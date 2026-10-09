"""ECAPA-TDNN speaker embedding backend (SpeechBrain, speechbrain/spkrec-ecapa-voxceleb).

Modern architecture (channel attention + attentive statistical pooling)
with ~0.80% EER on VoxCeleb1, vs Resemblyzer's much weaker discrimination
observed in practice on this project's real video audio (see conversation
history). Not gated -- no HuggingFace token required.

IMPORTANT: this backend's cosine similarity operates on a different scale
than Resemblyzer's. Measured on real audio (same methodology used to
diagnose Resemblyzer's false-merge issue): same-speaker ~0.62,
different-speaker ~0.15 -- a much wider separation margin than
Resemblyzer's ~0.92 vs ~0.64, but the absolute threshold is NOT
interchangeable between backends. SpeechBrain's own verify_batch() default
threshold is 0.25; this project defaults to 0.40 (within the measured
same/different gap, biased toward avoiding false new-speaker splits).
"""

from __future__ import annotations

import logging

import numpy as np
import torch
from speechbrain.inference.speaker import EncoderClassifier

logger = logging.getLogger("speaker_id.embeddings.ecapa")

from .base import EmbeddingBackend


def _pcm16_to_float_tensor(pcm16_mono: bytes) -> torch.Tensor:
    audio = np.frombuffer(pcm16_mono, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    return torch.from_numpy(audio)


class EcapaBackend(EmbeddingBackend):
    default_similarity_threshold = 0.40

    def __init__(self, device: str = "cuda", download_root: str | None = None) -> None:
        logger.info("loading ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb) on %s", device)
        savedir = f"{download_root}/speechbrain_ecapa" if download_root else None
        self._device = device
        self._classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": device},
            savedir=savedir,
        )
        logger.info("ECAPA-TDNN loaded")

    def embed(self, pcm16_mono: bytes) -> np.ndarray:
        wav = _pcm16_to_float_tensor(pcm16_mono).unsqueeze(0)
        with torch.no_grad():
            emb = self._classifier.encode_batch(wav.to(self._device))
        return emb.squeeze().cpu().numpy()

    def close(self) -> None:
        del self._classifier
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
