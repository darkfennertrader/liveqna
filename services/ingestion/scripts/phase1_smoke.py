from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

import httpx


def require(response: httpx.Response, expected: int) -> dict:
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url}: "
            f"expected {expected}, received {response.status_code}: {response.text}"
        )
    if not response.content:
        return {}
    return response.json()


def upload_version(
    client: httpx.Client,
    document_id: str,
    version_label: str,
    content: bytes,
) -> dict:
    return require(
        client.post(
            f"/documents/{document_id}/versions",
            data={"version_label": version_label},
            files={"file": ("architecture.txt", content, "text/plain")},
        ),
        201,
    )


def run(base_url: str) -> dict:
    suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    with httpx.Client(base_url=base_url, timeout=20) as client:
        project = require(
            client.post("/projects", json={"name": f"Phase 1 Review {suffix}"}),
            201,
        )
        document = require(
            client.post(
                f"/projects/{project['id']}/documents",
                json={
                    "title": "Client AI architecture",
                    "document_type": "official",
                },
            ),
            201,
        )

        version_1 = upload_version(
            client, document["id"], "v1", b"approved architecture version one\n"
        )
        assert version_1["status"] == "DRAFT"
        version_1 = require(
            client.post(f"/versions/{version_1['id']}/approve"), 200
        )
        assert version_1["status"] == "APPROVED"

        version_2 = upload_version(
            client, document["id"], "v2", b"approved architecture version two\n"
        )
        assert version_2["status"] == "DRAFT"

        duplicate = client.post(
            f"/documents/{document['id']}/versions",
            data={"version_label": "duplicate-content"},
            files={
                "file": (
                    "duplicate.txt",
                    b"approved architecture version two\n",
                    "text/plain",
                )
            },
        )
        require(duplicate, 409)

        version_2 = require(
            client.post(f"/versions/{version_2['id']}/approve"), 200
        )
        assert version_2["status"] == "APPROVED"
        assert version_2["supersedes_version_id"] == version_1["id"]

        version_3 = upload_version(
            client, document["id"], "v3", b"unneeded draft version three\n"
        )
        require(client.delete(f"/versions/{version_3['id']}"), 200)
        require(client.get(f"/versions/{version_3['id']}"), 404)
        require(client.delete(f"/versions/{version_2['id']}"), 409)
        require(client.post(f"/versions/{version_2['id']}/approve"), 409)

        versions = require(
            client.get(f"/documents/{document['id']}/versions"), 200
        )
        states = {item["version_label"]: item["status"] for item in versions}
        assert states == {"v1": "ARCHIVED", "v2": "APPROVED"}

    return {
        "project": project,
        "document": document,
        "versions": versions,
        "verified": {
            "new_version_starts_draft": True,
            "approval_archives_previous": True,
            "duplicate_content_rejected": True,
            "draft_deletion_allowed": True,
            "approved_deletion_rejected": True,
            "repeat_approval_rejected": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the Phase 1 lifecycle")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8084", help="ingestion URL"
    )
    args = parser.parse_args()
    print(json.dumps(run(args.base_url), indent=2))


if __name__ == "__main__":
    main()
