"""capture service -- enumerates PipeWire audio sources and streams the
selected one as PCM16 to the stt service.

Endpoints:
  GET  /health
  GET  /sources            -- live list of capturable endpoints
  POST /configure          -- set source/sample-rate/channels for the next start
  POST /start               -- begin capture (uses last /configure'd params)
  POST /stop                -- stop capture
  GET  /status             -- current capture state
"""

from __future__ import annotations

import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .pipewire import list_endpoints
from .recorder import CaptureSession

app = FastAPI(title="liveqna-capture")

session = CaptureSession()

_config = {
    "source_node": os.environ.get("CAPTURE_SOURCE_NODE") or None,
    "source_kind": "source",
    "sample_rate": int(os.environ.get("CAPTURE_SAMPLE_RATE", "16000")),
    "channels": int(os.environ.get("CAPTURE_CHANNELS", "1")),
}

STT_WS_URL = os.environ.get("STT_WS_URL", "ws://stt:8082")


class ConfigureRequest(BaseModel):
    source_node: str
    source_kind: str = "source"
    sample_rate: int | None = None
    channels: int | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/sources")
async def sources():
    endpoints = await list_endpoints()
    return {"sources": [e.to_dict() for e in endpoints]}


@app.post("/configure")
async def configure(req: ConfigureRequest):
    _config["source_node"] = req.source_node
    _config["source_kind"] = req.source_kind
    if req.sample_rate:
        _config["sample_rate"] = req.sample_rate
    if req.channels:
        _config["channels"] = req.channels
    return {"config": _config}


@app.post("/start")
async def start():
    if not _config["source_node"]:
        return JSONResponse({"error": "no source configured"}, status_code=400)
    await session.start(
        node_name=_config["source_node"],
        kind=_config["source_kind"],
        sample_rate=_config["sample_rate"],
        channels=_config["channels"],
        stt_ws_url=STT_WS_URL,
    )
    return {"status": "started", "node": _config["source_node"]}


@app.post("/stop")
async def stop():
    await session.stop()
    return {"status": "stopped"}


@app.get("/status")
async def status():
    return {
        "running": session.is_running,
        "active_node": session.active_node,
        "last_error": session.last_error,
        "config": _config,
    }


@app.websocket("/ws/level")
async def ws_level(ws: WebSocket) -> None:
    await ws.accept()
    session.level_subscribers.add(ws)
    try:
        while True:
            # keep the connection open; this socket is emit-only
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        session.level_subscribers.discard(ws)

