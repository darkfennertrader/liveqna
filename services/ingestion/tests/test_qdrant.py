import httpx
import pytest

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
