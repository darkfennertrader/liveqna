from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .agents import LLMQuestionGeneratorAgent, LLMSupervisorAgent
from .graph import build_graph
from .model_factory import ModelConfigurationError, ModelFactory
from .providers.registry import create_provider_registry
from .repository import RunRepository
from .schemas import (
    CreateRunRequest,
    ModelOptionsResponse,
    ModelSelection,
    ReviewDecision,
    RunAccepted,
    RunResponse,
    RunStatusResponse,
)
from .service import InvalidRunStateError, OrchestrationService, RunNotFoundError


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator.main")
provider_registry = create_provider_registry()


def _default_selection(prefix: str) -> ModelSelection:
    defaults = {
        "SUPERVISOR": ("gpt-6-luna", "none", "fast"),
        "QUESTION_AGENT": ("gpt-6.1-sol", "low", "fast"),
    }
    default_model, default_effort, default_tier = defaults[prefix]
    provider = os.environ.get(f"{prefix}_PROVIDER", "openai").strip().lower()
    temperature = os.environ.get(f"{prefix}_TEMPERATURE", "").strip()
    parameters: dict[str, str | float] = {}
    if temperature:
        parameters["temperature"] = float(temperature)
    if provider == "openai":
        parameters["reasoning_effort"] = os.environ.get(
            f"{prefix}_REASONING_EFFORT", default_effort
        )
        parameters["service_tier"] = os.environ.get(
            f"{prefix}_SERVICE_TIER", default_tier
        )
    selection = ModelSelection(
        provider=provider,
        model=os.environ.get(f"{prefix}_MODEL", default_model),
        parameters=parameters,
    )
    return provider_registry.normalize_selection(selection)


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("ORCHESTRATOR_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    repository = RunRepository(str(data_dir / "runs.sqlite"))
    await repository.setup()
    models = ModelFactory(provider_registry)
    async with AsyncSqliteSaver.from_conn_string(str(data_dir / "checkpoints.sqlite")) as checkpointer:
        graph = build_graph(
            supervisor=LLMSupervisorAgent(models),
            question_generator=LLMQuestionGeneratorAgent(models),
            checkpointer=checkpointer,
        )
        orchestration = OrchestrationService(
            graph=graph, repository=repository
        )
        app.state.orchestration = orchestration
        try:
            yield
        finally:
            await orchestration.shutdown()


app = FastAPI(title="liveqna-orchestrator", lifespan=lifespan)


def _service(request: Request) -> OrchestrationService:
    return request.app.state.orchestration


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/model-options", response_model=ModelOptionsResponse)
async def model_options() -> ModelOptionsResponse:
    return ModelOptionsResponse(
        providers=provider_registry.names,
        provider_catalog=provider_registry.descriptors(),
        supervisor_default=_default_selection("SUPERVISOR"),
        question_default=_default_selection("QUESTION_AGENT"),
    )


@app.post("/runs", response_model=RunAccepted, status_code=202)
async def create_run(payload: CreateRunRequest, request: Request) -> RunAccepted:
    try:
        payload = payload.model_copy(
            update={
                "supervisor_model": provider_registry.normalize_selection(
                    payload.supervisor_model
                ),
                "question_model": provider_registry.normalize_selection(
                    payload.question_model
                ),
            }
        )
        return await _service(request).enqueue_run(payload)
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("orchestration run failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/runs", response_model=list[RunResponse])
async def list_runs(request: Request) -> list[RunResponse]:
    return await _service(request).list_runs()


@app.delete("/runs")
async def clear_runs(request: Request) -> dict[str, int]:
    return {"cleared": await _service(request).clear_runs()}


@app.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, request: Request) -> RunResponse:
    try:
        return await _service(request).get_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.get("/runs/{run_id}/status", response_model=RunStatusResponse)
async def get_run_status(run_id: str, request: Request) -> RunStatusResponse:
    try:
        return await _service(request).get_run_status(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.post("/runs/{run_id}/review", response_model=RunResponse | RunAccepted)
async def review_run(
    run_id: str, payload: ReviewDecision, request: Request, response: Response
) -> RunResponse | RunAccepted:
    try:
        if payload.action == "regenerate":
            response.status_code = 202
            return await _service(request).enqueue_regeneration(run_id, payload)
        return await _service(request).review_run(run_id, payload)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except InvalidRunStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("orchestration review failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

