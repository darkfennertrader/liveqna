from __future__ import annotations

from uuid import UUID

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

    async def delete_document_points(
        self,
        collection: str,
        document_id: UUID,
        point_ids: list[UUID],
    ) -> None:
        response = await self._client.post(
            f"/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={
                "filter": {
                    "must": [
                        {
                            "key": "document_id",
                            "match": {"value": str(document_id)},
                        }
                    ]
                }
            },
        )
        # Before chunking is introduced the collection legitimately does not
        # exist. In that case there cannot be any points to clean up.
        if response.status_code == 404:
            return
        response.raise_for_status()
        if not point_ids:
            return
        response = await self._client.post(
            f"/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={"points": [str(point_id) for point_id in point_ids]},
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
