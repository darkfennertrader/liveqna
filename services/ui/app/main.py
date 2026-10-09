"""ui service -- serves the frontend and brokers between the browser and
the capture/stt services.

- Serves static/index.html + static/app.js
- WS /ws/client: browser control channel (select_source, update_capture_config,
  update_model_config, start, stop, refresh_sources) + relays live transcript
  events pushed from stt's /ws/transcript.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import proxy
from .config_store import ConfigStore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui.main")

app = FastAPI(title="liveqna-ui")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """Force revalidation on every /static/* fetch.

    FastAPI's StaticFiles already sends an ETag, so this doesn't disable
    caching outright -- it just stops the browser from trusting a
    previously-cached copy without checking back first. Without this,
    a browser tab left open across a `ui` redeploy can keep running a
    stale app.js indefinitely (observed in practice: a UI fix landed
    server-side but an already-open tab kept rendering the pre-fix
    behavior until a hard refresh -- see conversation history).
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

store = ConfigStore()

STT_WS_URL = os.environ.get("STT_WS_URL", "ws://stt:8082")

CAPTURE_URL = os.environ.get("CAPTURE_URL", "http://capture:8081")
CAPTURE_WS_URL = CAPTURE_URL.replace("http://", "ws://").replace("https://", "wss://")

browser_clients: set[WebSocket] = set()
_transcript_relay_task: asyncio.Task | None = None
_level_relay_task: asyncio.Task | None = None


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def _relay_transcripts_forever() -> None:
    """Keep a connection open to stt's /ws/transcript and fan every event
    out to all currently-connected browser clients. Reconnects on drop."""
    url = f"{STT_WS_URL}/ws/transcript"
    while True:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                logger.info("connected to stt transcript stream")
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    dead = []
                    for client in list(browser_clients):
                        try:
                            await client.send_json({"kind": "transcript", **payload})
                        except Exception:
                            dead.append(client)
                    for client in dead:
                        browser_clients.discard(client)
        except Exception:
            logger.warning("transcript relay connection lost, retrying in 2s")
            await asyncio.sleep(2)


async def _relay_levels_forever() -> None:
    """Keep a connection open to capture's /ws/level and fan every event
    out to all currently-connected browser clients. Reconnects on drop."""
    url = f"{CAPTURE_WS_URL}/ws/level"
    while True:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                logger.info("connected to capture level stream")
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    dead = []
                    for client in list(browser_clients):
                        try:
                            await client.send_json({"kind": "level", **payload})
                        except Exception:
                            dead.append(client)
                    for client in dead:
                        browser_clients.discard(client)
        except Exception:
            logger.warning("level relay connection lost, retrying in 2s")
            await asyncio.sleep(2)


@app.on_event("startup")
async def _startup() -> None:
    global _transcript_relay_task, _level_relay_task
    _transcript_relay_task = asyncio.create_task(_relay_transcripts_forever())
    _level_relay_task = asyncio.create_task(_relay_levels_forever())


@app.websocket("/ws/client")
async def ws_client(ws: WebSocket) -> None:
    await ws.accept()
    browser_clients.add(ws)
    try:
        await ws.send_json({"kind": "config", "config": store.state})
        try:
            sources = await proxy.get_sources()
            await ws.send_json({"kind": "sources", "sources": sources})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"capture unreachable: {exc}"})

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle_message(ws, msg)
    except WebSocketDisconnect:
        pass
    finally:
        browser_clients.discard(ws)


async def _handle_message(ws: WebSocket, msg: dict) -> None:
    action = msg.get("action")

    if action == "refresh_sources":
        try:
            sources = await proxy.get_sources()
            await ws.send_json({"kind": "sources", "sources": sources})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": str(exc)})

    elif action == "update_capture_config":
        patch = store.update_capture(msg.get("config", {}))
        try:
            await proxy.configure_capture(patch)
            await ws.send_json({"kind": "capture_configured", "config": patch})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"capture configure failed: {exc}"})

    elif action == "update_model_config":
        patch = store.update_model(msg.get("config", {}))
        try:
            result = await proxy.configure_stt(patch)
            await ws.send_json({"kind": "model_configured", "result": result})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"model configure failed: {exc}"})

    elif action == "start":
        try:
            result = await proxy.start_capture()
            await ws.send_json({"kind": "capture_started", "result": result})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"start failed: {exc}"})

    elif action == "stop":
        try:
            result = await proxy.stop_capture()
            await ws.send_json({"kind": "capture_stopped", "result": result})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"stop failed: {exc}"})

    elif action == "status":
        try:
            cap_status = await proxy.capture_status()
            stt_status = await proxy.stt_health()
            await ws.send_json({"kind": "status", "capture": cap_status, "stt": stt_status})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": str(exc)})

