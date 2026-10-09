"""Manage the `pw-record` subprocess and forward captured PCM to the STT
service over a WebSocket.

Lifecycle: `start()` launches `pw-record` targeting the configured node,
reads its stdout in small chunks on a background task, and relays each
chunk as a binary WebSocket frame to `stt`'s `/ws/audio` endpoint. `stop()`
terminates the subprocess and closes the outbound socket. Only one capture
session is active at a time per `capture` container instance, matching the
"choose a source per session" UX -- starting a new one implicitly stops
any previous one.

Alongside the audio relay, each chunk's RMS level (0.0-1.0 normalized) is
computed and broadcast to any `/ws/level` subscribers, independent of
transcription -- this gives the UI an immediate "audio is flowing"
indicator even before any words resolve into text.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np
import websockets
from fastapi import WebSocket

logger = logging.getLogger("capture.recorder")

CHUNK_BYTES = 3200  # ~100ms of 16kHz mono s16 audio (16000*2*0.1)


def _rms_level(pcm_s16: bytes) -> float:
    """Normalized RMS (0.0-1.0) of a chunk of PCM16 mono audio."""
    if not pcm_s16:
        return 0.0
    samples = np.frombuffer(pcm_s16, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    return min(1.0, rms / 32768.0)


class CaptureSession:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task | None = None
        self._ws: websockets.ClientConnection | None = None
        self.active_node: str | None = None
        self.last_error: str | None = None
        self.level_subscribers: set[WebSocket] = set()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None


    async def start(
        self,
        node_name: str,
        kind: str,
        sample_rate: int,
        channels: int,
        stt_ws_url: str,
    ) -> None:
        await self.stop()
        self.last_error = None

        cmd = [
            "pw-record",
            "--target",
            node_name,
            "--rate",
            str(sample_rate),
            "--channels",
            str(channels),
            "--format",
            "s16",
        ]
        if kind == "sink-monitor":
            cmd += ["-P", "{ stream.capture.sink=true }"]
        cmd.append("-")  # write raw PCM to stdout

        logger.info("starting capture: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._ws = await websockets.connect(f"{stt_ws_url}/ws/audio", max_size=None)
        self.active_node = node_name
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                chunk = await self._proc.stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                if self._ws is not None:
                    await self._ws.send(chunk)
                await self._broadcast_level(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface to UI via last_error
            logger.exception("capture pump failed")
            self.last_error = str(exc)

    async def _broadcast_level(self, chunk: bytes) -> None:
        if not self.level_subscribers:
            return
        level = _rms_level(chunk)
        dead = []
        for ws in list(self.level_subscribers):
            try:
                await ws.send_json({"level": level})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.level_subscribers.discard(ws)


    async def stop(self) -> None:
        # Terminate the subprocess first so its stdout/stderr pipes reach
        # EOF, which lets the pump task's read() calls return naturally
        # instead of blocking indefinitely on an open pipe.
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()

        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await asyncio.wait_for(self._pump_task, timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            self._pump_task = None

        if self._proc is not None and self._proc.stderr is not None:
            try:
                stderr = await asyncio.wait_for(self._proc.stderr.read(), timeout=1)
                if stderr:
                    logger.warning("pw-record stderr: %s", stderr.decode(errors="replace"))
            except (asyncio.TimeoutError, Exception):
                pass
        self._proc = None

        for ws in list(self.level_subscribers):
            try:
                await ws.send_json({"level": 0.0})
            except Exception:
                pass

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self.active_node = None

