"""Manages temporary uploaded enrollment videos: stream-to-disk upload,
duration probing, exact-range audio extraction, and expiry-based cleanup.

The uploaded video itself is never forwarded anywhere -- only the exact
audio range requested for each enrollment is extracted (via ffmpeg) and
sent on to speaker-id. The video file is deleted on explicit "finish" or
after an inactivity timeout, whichever comes first, so large uploads
don't accumulate on disk.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("ui.enrollment_uploads")

UPLOAD_DIR = "/tmp/liveqna_enrollment_uploads"
EXPIRY_SECONDS = 600  # 10 minutes of inactivity


@dataclass
class _Upload:
    path: str
    duration_sec: float
    last_used: float = field(default_factory=time.time)


class EnrollmentUploadManager:
    def __init__(self) -> None:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        self._uploads: dict[str, _Upload] = {}
        self._cleanup_task: asyncio.Task | None = None

    def start_background_cleanup(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [uid for uid, u in self._uploads.items() if now - u.last_used > EXPIRY_SECONDS]
            for uid in expired:
                self.discard(uid)

    async def save_upload(self, upload_id: str, upload_file) -> None:
        """Stream an UploadFile to disk in chunks without buffering the
        whole file in memory."""
        path = os.path.join(UPLOAD_DIR, f"{upload_id}.bin")
        chunk_size = 1024 * 1024  # 1 MiB
        with open(path, "wb") as f:
            while True:
                chunk = await upload_file.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
        duration = await asyncio.to_thread(self._probe_duration, path)
        self._uploads[upload_id] = _Upload(path=path, duration_sec=duration)

    def _probe_duration(self, path: str) -> float:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=30, check=True,
            )
            return float(result.stdout.strip())
        except Exception:
            logger.exception("ffprobe failed for %s", path)
            return 0.0

    def get_duration(self, upload_id: str) -> float | None:
        upload = self._uploads.get(upload_id)
        return upload.duration_sec if upload else None

    async def extract_pcm(self, upload_id: str, start_sec: float, end_sec: float,
                           sample_rate: int = 16000) -> bytes:
        """Extract [start_sec, end_sec) as mono PCM16 at `sample_rate`."""
        upload = self._uploads.get(upload_id)
        if upload is None:
            raise ValueError("unknown or expired upload_id")
        upload.last_used = time.time()

        duration = max(0.0, end_sec - start_sec)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-ss", str(start_sec), "-t", str(duration),
            "-i", upload.path,
            "-ar", str(sample_rate), "-ac", "1", "-f", "s16le", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg extraction failed: {stderr.decode(errors='replace')[-500:]}")
        return stdout

    def discard(self, upload_id: str) -> None:
        upload = self._uploads.pop(upload_id, None)
        if upload is not None:
            try:
                os.remove(upload.path)
            except OSError:
                pass

    def discard_all(self) -> None:
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        self._uploads.clear()


def new_upload_id() -> str:
    return str(uuid.uuid4())
