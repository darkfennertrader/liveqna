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
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import websockets
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import proxy
from .config_store import ConfigStore
from .live_session import LiveSessionConfig, LiveSessionManager, LiveSessionStateError


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
INGESTION_DOCUMENT_DISPLAY_ROOT = Path(
    os.environ.get(
        "INGESTION_DOCUMENT_DISPLAY_ROOT",
        "/home/ray/projects/liveqna/.data/ingestion/documents",
    )
)

browser_clients: set[WebSocket] = set()
_transcript_relay_task: asyncio.Task | None = None
_level_relay_task: asyncio.Task | None = None
_orchestration_watch_tasks: dict[str, asyncio.Task] = {}
_live_session_manager: LiveSessionManager | None = None
_runtime_lock = asyncio.Lock()
# STT eagerly preloads the configured Live Q&A backend at service startup.
_live_models_loaded = True


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


async def _submit_automatic_candidate(payload: dict) -> dict:
    result = await proxy.assess_automatic_candidate(payload)
    run_id = result.get("run_id")
    if run_id:
        _schedule_orchestration_watch(run_id)
    return result


async def _end_orchestration_session(session_id: str) -> None:
    await proxy.end_orchestration_session(session_id)


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
                    if _live_session_manager is not None:
                        await _live_session_manager.ingest(payload)
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
    global _transcript_relay_task, _level_relay_task, _live_models_loaded
    global _live_session_manager
    _live_session_manager = LiveSessionManager(
        config=LiveSessionConfig.from_env(),
        assess_candidate=_submit_automatic_candidate,
        emit_event=_broadcast_to_browsers,
        end_session_memory=_end_orchestration_session,
    )
    _transcript_relay_task = asyncio.create_task(_relay_transcripts_forever())
    _level_relay_task = asyncio.create_task(_relay_levels_forever())
    try:
        _live_models_loaded = bool((await proxy.stt_health()).get("model_loaded"))
    except Exception:
        logger.warning("could not determine initial STT runtime state")


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _live_session_manager is not None:
        await _live_session_manager.shutdown()


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
        if not _live_models_loaded:
            await ws.send_json(
                {
                    "kind": "model_configured",
                    "result": {
                        "status": "saved",
                        "loaded": False,
                        "detail": "configuration will load when Live Q&A starts",
                    },
                }
            )
            return
        try:
            result = await proxy.configure_stt(patch)
            await ws.send_json({"kind": "model_configured", "result": result})
        except Exception as exc:
            await ws.send_json({"kind": "error", "message": f"model configure failed: {exc}"})

    elif action == "start":
        if not _live_models_loaded:
            await ws.send_json(
                {
                    "kind": "error",
                    "message": "Start Live Q&A first so its model can be loaded",
                }
            )
            return
        try:
            result = await proxy.start_capture()
            await ws.send_json({"kind": "capture_started", "result": result})
        except Exception as exc:
            if _live_session_manager is not None:
                await _live_session_manager.end()
            await ws.send_json({"kind": "error", "message": f"start failed: {exc}"})

    elif action == "stop":
        try:
            result = await proxy.stop_capture()
            if _live_session_manager is not None:
                await _live_session_manager.end()
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


# --- live-session and question-review schemas -----------------------------

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
    project_id: str = Field(min_length=1, max_length=100)
    project_name: str = Field(min_length=1, max_length=200)
    supervisor_model: AgentModelSelection
    question_model: AgentModelSelection


class StartLiveRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    project_name: str = Field(min_length=1, max_length=200)
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


# --- local GPU runtime coordination ---------------------------------------

@app.get("/api/runtime/status")
async def runtime_status():
    try:
        ingestion, orchestration, stt = await asyncio.gather(
            proxy.ingestion_runtime_status(),
            proxy.orchestrator_runtime_status(),
            proxy.stt_health(),
        )
        return {
            "live_models_loaded": _live_models_loaded,
            "live_session": {
                "state": _live_session_manager.state,
                "config": _live_session_manager.config.as_dict(),
            }
            if _live_session_manager is not None
            else {"state": "unavailable", "config": {}},
            "stt": stt,
            "orchestration": orchestration,
            "ingestion": ingestion,
        }
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"runtime status failed: {exc}"
        ) from exc


@app.post("/api/runtime/start-ingestion")
async def start_ingestion_runtime():
    """Prepare GPU runtime for ingestion; remote-only ingestion stays parallel."""
    global _live_models_loaded
    async with _runtime_lock:
        try:
            ingestion = await proxy.ingestion_runtime_status()
            if not ingestion.get("uses_local_gpu"):
                return {
                    "status": "ready",
                    "mode": "parallel",
                    "detail": "remote-only ingestion; Live Q&A remains loaded",
                }

            orchestration = await proxy.orchestrator_runtime_status()
            if orchestration.get("active_run_count", 0):
                raise HTTPException(
                    status_code=409,
                    detail="wait for the active Live Q&A inference to finish",
                )
            # Local ingestion owns the GPU until it completes.
            await proxy.stop_capture()
            if _live_session_manager is not None:
                await _live_session_manager.end()
            await proxy.unload_stt()
            _live_models_loaded = False
            await proxy.unload_orchestrator_models()
            await _broadcast_to_browsers(
                {"kind": "capture_stopped", "result": {"status": "stopped"}}
            )
            return {"status": "ready", "mode": "local_ingestion"}
        except proxy.UpstreamServiceError as exc:
            _raise_upstream(exc)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"GPU preparation for ingestion failed: {exc}"
            ) from exc


@app.post("/api/runtime/start-live")
async def start_live_runtime(req: StartLiveRequest):
    """Load Live Q&A models and open an automatic transcript session."""
    global _live_models_loaded
    async with _runtime_lock:
        try:
            ingestion = await proxy.ingestion_runtime_status()
            if ingestion.get("uses_local_gpu"):
                if ingestion.get("active_extraction_count", 0):
                    raise HTTPException(
                        status_code=409,
                        detail="wait for local-model document ingestion to finish",
                    )
                await proxy.unload_ingestion_models()

            if _live_models_loaded:
                configured = await proxy.stt_health()
            else:
                model_config = dict(store.state["model"])
                model_config["sample_rate"] = store.state["capture"]["sample_rate"]
                configured = await proxy.configure_stt(model_config)
                _live_models_loaded = True
            if _live_session_manager is None:
                raise HTTPException(
                    status_code=503, detail="live session manager is unavailable"
                )
            session = await _live_session_manager.start(
                {
                    "project_id": req.project_id,
                    "project_name": req.project_name,
                    "supervisor_model": req.supervisor_model.model_dump(),
                    "question_model": req.question_model.model_dump(),
                }
            )
            return {
                "status": "ready",
                "mode": "live",
                "stt": configured,
                "session": session,
                "ingestion_was_remote_only": not ingestion.get("uses_local_gpu"),
            }
        except proxy.UpstreamServiceError as exc:
            _raise_upstream(exc)
        except HTTPException:
            raise
        except LiveSessionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Live Q&A model loading failed: {exc}"
            ) from exc


@app.post("/api/runtime/end-live")
async def end_live_runtime():
    """Stop capture and asynchronously flush the session's remaining text."""
    async with _runtime_lock:
        try:
            capture = await proxy.stop_capture()
            session = (
                await _live_session_manager.end()
                if _live_session_manager is not None
                else {"status": "idle", "session_id": None}
            )
            await _broadcast_to_browsers(
                {"kind": "capture_stopped", "result": capture}
            )
            return {"status": "ending", "capture": capture, "session": session}
        except proxy.UpstreamServiceError as exc:
            _raise_upstream(exc)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"ending Live Q&A failed: {exc}"
            ) from exc


async def _find_or_create_ingestion_project(name: str) -> dict:
    projects = await proxy.list_ingestion_projects()
    existing = next(
        (project for project in projects if project["name"].casefold() == name.casefold()),
        None,
    )
    if existing is not None:
        return existing
    try:
        return await proxy.create_ingestion_project(name)
    except proxy.UpstreamServiceError as exc:
        if exc.status_code != 409:
            raise
        projects = await proxy.list_ingestion_projects()
        existing = next(
            (
                project
                for project in projects
                if project["name"].casefold() == name.casefold()
            ),
            None,
        )
        if existing is None:
            raise
        return existing


async def _find_or_create_ingestion_document(
    project_id: str, filename: str, content_profile: str
) -> dict:
    documents = await proxy.list_ingestion_documents(project_id)
    existing = next(
        (document for document in documents if document["title"] == filename),
        None,
    )
    if existing is not None:
        return existing
    try:
        return await proxy.create_ingestion_document(
            project_id, filename, content_profile
        )
    except proxy.UpstreamServiceError as exc:
        if exc.status_code != 409:
            raise
        documents = await proxy.list_ingestion_documents(project_id)
        existing = next(
            (document for document in documents if document["title"] == filename),
            None,
        )
        if existing is None:
            raise
        return existing


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
                "project_id": req.project_id,
                "project_name": req.project_name,
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


# --- document ingestion ----------------------------------------------------

@app.get("/api/ingestion/projects")
async def ingestion_projects():
    try:
        return await proxy.list_ingestion_projects()
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)

@app.post("/api/ingestion/upload")
async def upload_for_ingestion(
    file: UploadFile = File(...),
    project_name: str = Form("LiveQnA"),
    content_profile: str = Form("general_document"),
    retrieval_metadata: str = Form("{}"),
):
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="uploaded file needs a filename")
    try:
        parsed_metadata = json.loads(retrieval_metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="retrieval metadata must be valid JSON"
        ) from exc
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(
            status_code=400, detail="retrieval metadata must be a JSON object"
        )

    clean_project = project_name.strip() or "LiveQnA"
    clean_profile = content_profile.strip().lower() or "general_document"
    version_label = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    try:
        project = await _find_or_create_ingestion_project(clean_project)
        document = await _find_or_create_ingestion_document(
            str(project["id"]), filename, clean_profile
        )
        version = await proxy.upload_ingestion_version(
            str(document["id"]),
            version_label=version_label,
            content_profile=clean_profile,
            retrieval_metadata=json.dumps(parsed_metadata),
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            file_object=file.file,
        )
        return {"project": project, "document": document, "version": version}
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)
    finally:
        await file.close()


@app.delete("/api/ingestion/documents/{document_id}", status_code=202)
async def delete_ingestion_document(document_id: str):
    try:
        return await proxy.delete_ingestion_document(document_id)
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.get("/api/ingestion/library")
async def ingestion_library():
    try:
        projects = await proxy.list_ingestion_projects()
        result: list[dict] = []
        for project in projects:
            documents = await proxy.list_ingestion_documents(str(project["id"]))
            for document in documents:
                versions = await proxy.list_ingestion_versions(str(document["id"]))
                result.append(
                    {"project": project, "document": document, "versions": versions}
                )
        return result
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.get("/api/ingestion/versions/{version_id}")
async def ingestion_version_status(version_id: str):
    try:
        version, extraction = await asyncio.gather(
            proxy.get_ingestion_version(version_id),
            proxy.get_ingestion_extraction(version_id),
        )
        source_path = version.get("file_path")
        if source_path:
            relative = Path(source_path)
            if not relative.is_absolute() and ".." not in relative.parts:
                extraction["source_full_path"] = str(
                    INGESTION_DOCUMENT_DISPLAY_ROOT / relative
                )
        return {"version": version, "extraction": extraction}
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.get("/api/ingestion/versions/{version_id}/normalized")
async def ingestion_normalized_document(version_id: str):
    try:
        return await proxy.get_normalized_document(version_id)
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.post("/api/ingestion/versions/{version_id}/reingest", status_code=202)
async def reingest_version(version_id: str):
    try:
        return await proxy.reingest_version(version_id)
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.post("/api/ingestion/versions/{version_id}/approve")
async def approve_ingestion_version(version_id: str):
    try:
        return await proxy.approve_ingestion_version(version_id)
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)


@app.delete("/api/ingestion/versions/{version_id}")
async def reject_ingestion_version(version_id: str):
    """Rejecting an unapproved version deletes that draft and its artifacts."""
    try:
        return await proxy.delete_ingestion_draft(version_id)
    except proxy.UpstreamServiceError as exc:
        _raise_upstream(exc)

