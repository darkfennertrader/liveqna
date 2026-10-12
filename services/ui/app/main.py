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
from typing import Literal

import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import proxy
from .config_store import ConfigStore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui.main")

app = FastAPI(title="liveqna-ui")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    """Force revalidation of the app shell and its static assets.

    FastAPI's StaticFiles already sends an ETag, so this doesn't disable
    caching outright -- it just stops the browser from trusting a
    previously-cached copy without checking back first. Without this,
    a browser tab left open across a `ui` redeploy can keep running a
    stale app.js indefinitely (observed in practice: a UI fix landed
    server-side but an already-open tab kept rendering the pre-fix
    behavior until a hard refresh -- see conversation history).
    """
    response = await call_next(request)
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
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
_orchestration_watch_tasks: dict[str, asyncio.Task] = {}


async def _broadcast_to_browsers(payload: dict) -> None:
    dead = []
    for client in list(browser_clients):
        try:
            await client.send_json(payload)
        except Exception:
            dead.append(client)
    for client in dead:
        browser_clients.discard(client)


async def _watch_orchestration_run(run_id: str) -> None:
    try:
        while True:
            await asyncio.sleep(0.2)
            try:
                status = await proxy.get_orchestration_run_status(run_id)
            except proxy.UpstreamServiceError as exc:
                if exc.status_code == 404:
                    return
                continue
            if status["status"] in {"generating", "supervising"}:
                continue
            if status["status"] == "failed":
                await _broadcast_to_browsers(
                    {
                        "kind": "orchestration_run_failed",
                        "run_id": run_id,
                        "error": status.get("error") or "question generation failed",
                    }
                )
                return
            try:
                run = await proxy.get_orchestration_run(run_id)
            except proxy.UpstreamServiceError:
                continue
            await _broadcast_to_browsers(
                {"kind": "orchestration_run", "run": run}
            )
            return
    finally:
        _orchestration_watch_tasks.pop(run_id, None)


def _schedule_orchestration_watch(run_id: str) -> None:
    existing = _orchestration_watch_tasks.get(run_id)
    if existing and not existing.done():
        return
    _orchestration_watch_tasks[run_id] = asyncio.create_task(
        _watch_orchestration_run(run_id)
    )


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


# --- push-to-talk capture (manual: user marks a time window, then sends
# the captured text for downstream processing) ---------------------------

class AgentModelSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    parameters: dict[str, str | bool | int | float | None] = Field(
        default_factory=dict
    )
    # Accepted temporarily for compatibility with an already-open older UI.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    reasoning_effort: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] | None = None
    service_tier: Literal[
        "default", "fast", "ultrafast", "flex"
    ] | None = None


class PushToTalkSendRequest(BaseModel):
    text: str = Field(min_length=1, max_length=30_000)
    supervisor_model: AgentModelSelection
    question_model: AgentModelSelection


class QuestionDraft(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2_000)
    source_excerpt: str = Field(default="", max_length=2_000)
    review_status: Literal["pending", "validated", "rejected"] = "pending"


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject", "regenerate"]
    question_id: str = Field(min_length=1, max_length=100)
    questions: list[QuestionDraft] = Field(default_factory=list, max_length=20)
    feedback: str = Field(default="", max_length=2_000)


def _raise_upstream(exc: proxy.UpstreamServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/push_to_talk/send")
async def push_to_talk_send(req: PushToTalkSendRequest):
    """Create a durable question-generation run from captured text."""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="no text captured")
    logger.info("push-to-talk send: %d chars", len(text))
    try:
        accepted = await proxy.create_orchestration_run(
            {
                "text": text,
                "supervisor_model": req.supervisor_model.model_dump(),
                "question_model": req.question_model.model_dump(),
            }
        )
        _schedule_orchestration_watch(accepted["run_id"])
        return accepted
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.get("/api/orchestrator/model-options")
async def orchestrator_model_options():
    try:
        return await proxy.orchestrator_model_options()
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.get("/api/orchestrator/runs")
async def orchestration_runs():
    try:
        return await proxy.list_orchestration_runs()
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.delete("/api/orchestrator/runs")
async def clear_orchestration_runs():
    try:
        return await proxy.clear_orchestration_runs()
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.get("/api/orchestrator/runs/{run_id}")
async def orchestration_run(run_id: str):
    try:
        return await proxy.get_orchestration_run(run_id)
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.post("/api/orchestrator/runs/{run_id}/review")
async def review_orchestration_run(run_id: str, req: ReviewRequest):
    try:
        result = await proxy.review_orchestration_run(run_id, req.model_dump())
        if result.get("accepted"):
            _schedule_orchestration_watch(run_id)
        return result
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)

