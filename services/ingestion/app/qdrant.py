from __future__ import annotations

import httpx


class QdrantProbe:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
        )

    async def ready(self) -> bool:
        response = await self._client.get("/readyz")
        response.raise_for_status()
        return True

    async def close(self) -> None:
        await self._client.aclose()
