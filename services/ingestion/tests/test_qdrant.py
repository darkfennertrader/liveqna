import httpx
import pytest
from uuid import UUID

from app.qdrant import QdrantProbe


@pytest.mark.asyncio
async def test_qdrant_probe_accepts_ready_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/readyz"
        return httpx.Response(200, text="healthz check passed")

    probe = QdrantProbe(
        "http://qdrant:6333",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await probe.ready() is True
    finally:
        await probe.close()


@pytest.mark.asyncio
async def test_qdrant_deletes_explicit_document_points() -> None:
    point_id = UUID("11111111-1111-1111-1111-111111111111")
    document_id = UUID("22222222-2222-2222-2222-222222222222")
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/collections/liveqna_chunks/points/delete"
        assert request.url.params["wait"] == "true"
        if calls == 1:
            assert b'"key":"document_id"' in request.content
            assert str(document_id).encode() in request.content
        else:
            assert request.content == (
                b'{"points":["11111111-1111-1111-1111-111111111111"]}'
            )
        return httpx.Response(200, json={"status": "ok"})

    probe = QdrantProbe(
        "http://qdrant:6333",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        await probe.delete_document_points(
            "liveqna_chunks", document_id, [point_id]
        )
        assert calls == 2
    finally:
        await probe.close()
