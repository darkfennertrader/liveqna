from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore


SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.graph import build_graph  # noqa: E402


class DiagramOnlyAgent:
    """The renderer inspects topology and never invokes agent methods."""

    async def assess(self, **kwargs):  # pragma: no cover
        raise RuntimeError("diagram-only agent")

    async def delegate(self, **kwargs):  # pragma: no cover
        raise RuntimeError("diagram-only agent")

    async def generate(self, **kwargs):  # pragma: no cover
        raise RuntimeError("diagram-only agent")


def main() -> None:
    output_dir = Path(__file__).resolve().parent
    agent = DiagramOnlyAgent()
    compiled = build_graph(
        supervisor=agent,
        question_generator=agent,
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    drawable = compiled.get_graph()
    source_path = output_dir / "orchestrator-langgraph.mmd"
    image_path = output_dir / "orchestrator-langgraph.png"
    source_path.write_text(drawable.draw_mermaid(), encoding="utf-8")
    # LangGraph's direct PNG API sends the topology to mermaid.ink. Keep the
    # LangGraph-generated source local and render it with the installed CLI.
    render_env = dict(os.environ)
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if chrome:
        render_env["PUPPETEER_EXECUTABLE_PATH"] = chrome
    subprocess.run(
        [
            "mmdc",
            "-i",
            str(source_path),
            "-o",
            str(image_path),
            "-b",
            "white",
            "-w",
            "1800",
        ],
        check=True,
        env=render_env,
    )


if __name__ == "__main__":
    main()
