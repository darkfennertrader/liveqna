from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.extraction.models import (
    SlideReconciliation,
    SlideReviewFinding,
    SlideReviewResult,
    SlideVisionResult,
)
from app.vision.ollama import OllamaVisionProvider
from app.vision.openai import OpenAIVisionProvider
from app.vision.prompt import SYSTEM_INSTRUCTIONS


def vision_result() -> SlideVisionResult:
    return SlideVisionResult(
        transcription_markdown="# Architecture",
        tables_markdown=[],
        visual_description="A component diagram.",
        labels=["architecture"],
        discrepancies=[],
        uncertainties=[],
    )


def review_result() -> SlideReviewResult:
    return SlideReviewResult(
        summary="Evidence agrees.",
        findings=[
            SlideReviewFinding(
                category="NO_DISCREPANCY",
                description="No discrepancy found.",
                evidence="Native and rendered evidence agree.",
            )
        ],
    )


def test_prompt_forbids_guessing_obscured_diagram_text() -> None:
    normalized_prompt = " ".join(SYSTEM_INSTRUCTIONS.split())
    assert "Never reconstruct text that is covered" in normalized_prompt
    assert "without inventing the hidden component name" in normalized_prompt
    assert "normalized 0-1000 slide coordinates" in normalized_prompt


def test_openai_provider_sends_png_and_uses_structured_output(
    tmp_path: Path,
) -> None:
    captured = {}

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=vision_result())

    image = tmp_path / "slide.png"
    image.write_bytes(b"png")
    provider = OpenAIVisionProvider(
        api_key="test",
        model="test-model",
        client=SimpleNamespace(responses=Responses()),
    )

    result = provider.analyze_slide(
        image,
        slide_number=1,
        content_profile="qbr",
        native_markdown="# Native",
        native_elements=[
            {
                "element_id": "slide-0001-shape-0002",
                "kind": "title",
                "content": "# Native",
                "bbox": {"x": 10, "y": 20, "width": 300, "height": 80},
                "block_ids": ["b000001"],
            }
        ],
        prompt_version="test-prompt",
    )

    assert result.labels == ["architecture"]
    assert captured["text_format"] is SlideVisionResult
    prompt = captured["input"][0]["content"][0]["text"]
    assert "Analyze only the rendered slide" in prompt
    assert "slide-0001-shape-0002" not in prompt
    assert "# Native" not in prompt
    image_input = captured["input"][0]["content"][1]
    assert image_input["image_url"].startswith("data:image/png;base64,")
    assert image_input["detail"] == "high"
    assert captured["store"] is False


def test_openai_provider_review_call_uses_native_and_reconciliation(
    tmp_path: Path,
) -> None:
    captured = {}

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=review_result())

    image = tmp_path / "slide.png"
    image.write_bytes(b"png")
    provider = OpenAIVisionProvider(
        api_key="test",
        model="review-model",
        client=SimpleNamespace(responses=Responses()),
    )
    result = provider.review_slide(
        image,
        slide_number=3,
        content_profile="general_presentation",
        native_markdown="# Native value 42",
        native_elements=[],
        vision=vision_result(),
        reconciliation=SlideReconciliation(retrieval_markdown="# Native value 42"),
        prompt_version="pptx-slide-review-v1",
    )

    assert result.findings[0].category == "NO_DISCREPANCY"
    assert captured["text_format"] is SlideReviewResult
    assert "Native value 42" in captured["input"][0]["content"][0]["text"]
    assert "authoritative" in captured["instructions"]


def test_openai_provider_uses_pdf_page_prompt(tmp_path: Path) -> None:
    captured = {}

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=vision_result())

    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    provider = OpenAIVisionProvider(
        api_key="test",
        model="test-model",
        client=SimpleNamespace(responses=Responses()),
    )

    provider.analyze_page(
        image,
        page_number=7,
        content_profile="architecture",
        native_markdown="Native page text",
        prompt_version="pdf-page-v1",
    )

    prompt = captured["input"][0]["content"][0]["text"]
    assert "PDF page number: 7" in prompt
    assert "Native PDF extraction" in prompt
    assert "Do not infer speaker attribution" in captured["instructions"]


def test_ollama_provider_sends_image_and_json_schema(tmp_path: Path) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.update(payload)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": vision_result().model_dump_json(),
                }
            },
        )

    image = tmp_path / "slide.png"
    image.write_bytes(b"png")
    client = httpx.Client(
        base_url="http://ollama:11434",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaVisionProvider(
        model="local-vision", base_url="http://ollama:11434", client=client
    )

    result = provider.analyze_slide(
        image,
        slide_number=2,
        content_profile="general_presentation",
        native_markdown="# Native",
        native_elements=[],
        prompt_version="test-prompt",
    )

    assert result.visual_description == "A component diagram."
    assert captured["stream"] is False
    assert captured["messages"][1]["images"]
    assert captured["format"]["additionalProperties"] is False
