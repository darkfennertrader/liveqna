"""ui service -- serves the frontend and brokers between the browser and
the capture/stt services.

- Serves static/index.html + static/app.js
- WS /ws/client: browser control channel (select_source, update_capture_config,
  update_model_config, start, stop, refresh_sources) + relays live transcript
  events pushed from stt's /ws/transcript.
- REST endpoints for speaker enrollment (upload a video, extract named
  clips from it, forward to speaker-id, list/remove enrolled speakers).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import websockets
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import proxy
from .config_store import ConfigStore
from .enrollment_uploads import EnrollmentUploadManager, new_upload_id

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
uploads = EnrollmentUploadManager()

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
    uploads.start_background_cleanup()


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
            await proxy.reset_speaker_id()
        except Exception:
            logger.warning("speaker-id reset failed (continuing anyway)", exc_info=True)
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

    elif action == "update_speaker_config":
        patch = store.update_speaker(msg.get("config", {}))
        try:
            await proxy.configure_speaker_id(patch)
            await ws.send_json({"kind": "speaker_configured", "config": patch})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"speaker configure failed: {exc}"})

    elif action == "merge_speaker":
        source_id = msg.get("source_id")
        target_id = msg.get("target_id")
        if not source_id or not target_id:
            await ws.send_json({"kind": "error", "message": "merge_speaker requires source_id and target_id"})
            return
        try:
            result = await proxy.merge_speaker(source_id, target_id)
            await ws.send_json({"kind": "speaker_merged", "result": result})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"merge failed: {exc}"})


# --- speaker enrollment (upload once, enroll multiple people) -----------

class EnrollFromUploadRequest(BaseModel):
    upload_id: str
    name: str
    start_sec: float
    end_sec: float


@app.post("/api/enrollment/upload")
async def upload_enrollment_video(file: UploadFile):
    upload_id = new_upload_id()
    try:
        await uploads.save_upload(upload_id, file)
    except Exception as exc:
        logger.exception("upload failed")
        return JSONResponse({"error": str(exc)}, status_code=400)

    duration = uploads.get_duration(upload_id)
    if not duration:
        uploads.discard(upload_id)
        return JSONResponse({"error": "could not read video duration (unsupported or corrupt file?)"}, status_code=400)

    return {"upload_id": upload_id, "duration_sec": duration}


@app.post("/api/enrollment/enroll")
async def enroll_from_upload(req: EnrollFromUploadRequest):
    try:
        pcm = await uploads.extract_pcm(req.upload_id, req.start_sec, req.end_sec)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("audio extraction failed")
        return JSONResponse({"error": f"extraction failed: {exc}"}, status_code=400)

    audio_b64 = base64.b64encode(pcm).decode("ascii")
    result = await proxy.enroll_speaker(
        req.name, audio_b64,
        source_label=req.upload_id, start_sec=req.start_sec, end_sec=req.end_sec,
    )
    if "error" in result:
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/enrollment/finish/{upload_id}")
async def finish_enrollment_upload(upload_id: str):
    uploads.discard(upload_id)
    return {"status": "discarded"}


@app.get("/api/enrollment/speakers")
async def get_enrolled_speakers():
    try:
        speakers = await proxy.list_enrolled_speakers()
    except Exception as exc:
        return JSONResponse({"error": str(exc), "speakers": []}, status_code=400)
    return {"speakers": speakers}


@app.delete("/api/enrollment/speakers/{name}")
async def remove_enrolled_speaker(name: str):
    try:
        result = await proxy.delete_enrolled_speaker(name)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return result
