"""PipeWire source enumeration via `pw-dump`.

This module shells out to the `pw-dump` CLI (part of `pipewire-bin`, already
present on the host and installed in this service's image) rather than
binding to libpipewire directly. `pw-dump` prints the entire PipeWire object
graph as JSON over a single call, which is more than enough to build a
"pick a source" list and is far simpler/more stable than writing against the
native C API from Python.

Two kinds of selectable endpoint are exposed:
  * "source"       -- a hardware Audio/Source node (mic, line-in, capture
                       card input). Recorded directly.
  * "sink-monitor"  -- the monitor of an Audio/Sink (i.e. "listen to
                       whatever is currently playing through this output").
                       Recorded with the `stream.capture.sink=true` node
                       property set on `pw-record`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger("capture.pipewire")


@dataclass
class AudioEndpoint:
    node_name: str
    description: str
    kind: str  # "source" | "sink-monitor"

    def to_dict(self) -> dict:
        return {
            "node_name": self.node_name,
            "description": self.description,
            "kind": self.kind,
        }


async def _run_pw_dump() -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        "pw-dump",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pw-dump failed: {stderr.decode(errors='replace')}")
    return json.loads(stdout.decode(errors="replace"))


async def list_endpoints() -> list[AudioEndpoint]:
    """Enumerate every currently capturable audio endpoint.

    Re-runs `pw-dump` on every call (deliberately not cached) so the UI's
    "refresh sources" action always reflects whatever is plugged in / torn
    down right now.
    """
    try:
        objects = await _run_pw_dump()
    except Exception:
        logger.exception("failed to enumerate PipeWire nodes")
        return []

    endpoints: list[AudioEndpoint] = []
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        media_class = props.get("media.class", "")
        node_name = props.get("node.name")
        if not node_name:
            continue
        description = props.get("node.description") or props.get("node.nick") or node_name

        if media_class == "Audio/Source":
            endpoints.append(AudioEndpoint(node_name, description, "source"))
        elif media_class == "Audio/Sink":
            endpoints.append(
                AudioEndpoint(node_name, f"{description} (monitor)", "sink-monitor")
            )

    endpoints.sort(key=lambda e: (e.kind, e.description.lower()))
    return endpoints
