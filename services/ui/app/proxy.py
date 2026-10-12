"""Thin REST/WS client wrapping calls to the capture and stt services."""

from __future__ import annotations

import os

import httpx

CAPTURE_URL = os.environ.get("CAPTURE_URL", "http://capture:8081")
STT_URL = os.environ.get("STT_URL", "http://stt:8082")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8083")


class UpstreamServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


async def _orchestrator_request(method: str, path: str, payload: dict | None = None):
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.request(
            method,
            f"{ORCHESTRATOR_URL}{path}",
            json=payload,
        )
    if response.is_error:
        try:
            body = response.json()
            detail = body.get("detail") or body.get("error") or response.text
        except ValueError:
            detail = response.text
        raise UpstreamServiceError(
            str(detail or "orchestrator request failed"), response.status_code
        )
    return response.json()


async def get_sources() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{CAPTURE_URL}/sources")
        resp.raise_for_status()
        return resp.json().get("sources", [])


async def configure_capture(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{CAPTURE_URL}/configure", json=payload)
        resp.raise_for_status()
        return resp.json()


async def start_capture() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{CAPTURE_URL}/start")
        resp.raise_for_status()
        return resp.json()


async def stop_capture() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{CAPTURE_URL}/stop")
        resp.raise_for_status()
        return resp.json()


async def capture_status() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{CAPTURE_URL}/status")
        resp.raise_for_status()
        return resp.json()


async def configure_stt(payload: dict) -> dict:
    # Model loading (local backend) can take a few seconds on first use.
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{STT_URL}/configure", json=payload)
        resp.raise_for_status()
        return resp.json()


async def stt_health() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{STT_URL}/health")
        resp.raise_for_status()
        return resp.json()


async def orchestrator_model_options() -> dict:
    return await _orchestrator_request("GET", "/model-options")


async def create_orchestration_run(payload: dict) -> dict:
    return await _orchestrator_request("POST", "/runs", payload)


async def list_orchestration_runs() -> list[dict]:
    return await _orchestrator_request("GET", "/runs")


async def clear_orchestration_runs() -> dict:
    return await _orchestrator_request("DELETE", "/runs")


async def get_orchestration_run(run_id: str) -> dict:
    return await _orchestrator_request("GET", f"/runs/{run_id}")


async def get_orchestration_run_status(run_id: str) -> dict:
    return await _orchestrator_request("GET", f"/runs/{run_id}/status")


async def review_orchestration_run(run_id: str, payload: dict) -> dict:
    return await _orchestrator_request("POST", f"/runs/{run_id}/review", payload)

