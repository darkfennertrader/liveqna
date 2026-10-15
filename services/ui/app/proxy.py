"""Thin REST/WS client wrapping calls to the capture and stt services."""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

CAPTURE_URL = os.environ.get("CAPTURE_URL", "http://capture:8081")
STT_URL = os.environ.get("STT_URL", "http://stt:8082")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8083")
INGESTION_URL = os.environ.get("INGESTION_URL", "http://ingestion:8084")


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


async def unload_stt() -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{STT_URL}/runtime/unload")
        resp.raise_for_status()
        return resp.json()


async def orchestrator_model_options() -> dict:
    return await _orchestrator_request("GET", "/model-options")


async def orchestrator_runtime_status() -> dict:
    return await _orchestrator_request("GET", "/runtime/status")


async def unload_orchestrator_models() -> dict:
    return await _orchestrator_request("POST", "/runtime/unload-models")


async def create_orchestration_run(payload: dict) -> dict:
    return await _orchestrator_request("POST", "/runs", payload)


async def assess_automatic_candidate(payload: dict) -> dict:
    return await _orchestrator_request("POST", "/automatic-candidates", payload)


async def end_orchestration_session(session_id: str) -> dict:
    return await _orchestrator_request("POST", f"/sessions/{session_id}/end")


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


async def _ingestion_request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    data: dict | None = None,
    files: dict | None = None,
    timeout: float = 120,
):
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method,
            f"{INGESTION_URL}{path}",
            json=payload,
            data=data,
            files=files,
        )
    if response.is_error:
        try:
            body = response.json()
            detail = body.get("detail") or body.get("error") or response.text
        except ValueError:
            detail = response.text
        raise UpstreamServiceError(
            str(detail or "ingestion request failed"), response.status_code
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


async def list_ingestion_projects() -> list[dict]:
    return await _ingestion_request("GET", "/projects")


async def ingestion_runtime_status() -> dict:
    return await _ingestion_request("GET", "/runtime/status")


async def unload_ingestion_models() -> dict:
    return await _ingestion_request("POST", "/runtime/unload-models")


async def clear_ingestion_review_runs_cache() -> dict:
    return await _ingestion_request("DELETE", "/review-runs-cache")


async def create_ingestion_project(name: str) -> dict:
    return await _ingestion_request("POST", "/projects", payload={"name": name})


async def delete_ingestion_project(project_id: str) -> dict:
    return await _ingestion_request("DELETE", f"/projects/{project_id}")


async def list_ingestion_documents(project_id: str) -> list[dict]:
    return await _ingestion_request("GET", f"/projects/{project_id}/documents")


async def create_ingestion_document(
    project_id: str, title: str, content_profile: str
) -> dict:
    # document_type is a legacy governance field in the ingestion registry.
    # Source format is inferred by the extractor from the uploaded extension.
    internal_type = "transcript" if content_profile == "transcript" else "official"
    return await _ingestion_request(
        "POST",
        f"/projects/{project_id}/documents",
        payload={
            "title": title,
            "document_type": internal_type,
            "content_profile": content_profile,
        },
    )


async def delete_ingestion_document(document_id: str) -> dict:
    return await _ingestion_request("DELETE", f"/documents/{document_id}")


async def list_ingestion_versions(document_id: str) -> list[dict]:
    return await _ingestion_request("GET", f"/documents/{document_id}/versions")


async def upload_ingestion_version(
    document_id: str,
    *,
    version_label: str,
    content_profile: str,
    retrieval_metadata: str,
    filename: str,
    content_type: str,
    file_object,
) -> dict:
    return await _ingestion_request(
        "POST",
        f"/documents/{document_id}/versions",
        data={
            "version_label": version_label,
            "content_profile": content_profile,
            "retrieval_metadata": retrieval_metadata,
        },
        files={"file": (filename, file_object, content_type)},
    )


async def get_ingestion_version(version_id: str) -> dict:
    return await _ingestion_request("GET", f"/versions/{version_id}")


async def get_ingestion_extraction(version_id: str) -> dict:
    return await _ingestion_request("GET", f"/versions/{version_id}/extraction")


async def reingest_version(version_id: str) -> dict:
    return await _ingestion_request("POST", f"/versions/{version_id}/extract")


async def get_normalized_document(version_id: str) -> dict:
    return await _ingestion_request("GET", f"/versions/{version_id}/normalized")


async def get_ingestion_review_report(version_id: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{INGESTION_URL}/versions/{version_id}/review-report"
        )
    _raise_ingestion_error(response)
    return response.text


async def get_ingestion_review_workflow(version_id: str) -> dict:
    return await _ingestion_request(
        "GET", f"/versions/{version_id}/review-workflow"
    )


async def resolve_ingestion_review_question(
    version_id: str, question_id: str, answer: str
) -> dict:
    return await _ingestion_request(
        "POST",
        f"/versions/{version_id}/review-questions/{question_id}/resolve",
        payload={"answer": answer},
        timeout=180,
    )


async def apply_ingestion_review_resolution(
    version_id: str, question_id: str
) -> dict:
    return await _ingestion_request(
        "POST",
        f"/versions/{version_id}/review-questions/{question_id}/apply",
    )


async def get_ingestion_review_artifact(
    version_id: str, artifact_path: str
) -> tuple[bytes, str]:
    encoded_path = quote(artifact_path, safe="/")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{INGESTION_URL}/versions/{version_id}"
            f"/review-artifacts/{encoded_path}"
        )
    _raise_ingestion_error(response)
    return response.content, response.headers.get("content-type", "image/png")


async def approve_ingestion_version(version_id: str) -> dict:
    return await _ingestion_request("POST", f"/versions/{version_id}/approve")


async def delete_ingestion_draft(version_id: str) -> dict:
    return await _ingestion_request("DELETE", f"/versions/{version_id}")


def _raise_ingestion_error(response: httpx.Response) -> None:
    if not response.is_error:
        return
    try:
        body = response.json()
        detail = body.get("detail") or body.get("error") or response.text
    except ValueError:
        detail = response.text
    raise UpstreamServiceError(
        str(detail or "ingestion request failed"), response.status_code
    )

