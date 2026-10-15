from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from pydantic import BaseModel

from ..extraction.models import (
    SlideReconciliation,
    SlideResolutionResult,
    SlideReviewDecisionFinding,
    SlideReviewResult,
    SlideVisionResult,
)
from .base import VisionProviderError
from .prompt import (
    PDF_SYSTEM_INSTRUCTIONS,
    SYSTEM_INSTRUCTIONS,
    page_prompt,
    REVIEW_SYSTEM_INSTRUCTIONS,
    RESOLUTION_SYSTEM_INSTRUCTIONS,
    review_prompt,
    resolution_prompt,
    slide_prompt,
)


class OllamaVisionProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        temperature: float | None = None,
        base_url: str,
        timeout_seconds: float = 180.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._temperature = temperature
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    def analyze_slide(
        self,
        image_path: Path,
        *,
        slide_number: int,
        content_profile: str,
        native_markdown: str,
        native_elements: list[dict[str, Any]],
        prompt_version: str,
    ) -> SlideVisionResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=SYSTEM_INSTRUCTIONS,
            prompt=slide_prompt(
                slide_number=slide_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                native_elements=native_elements,
            ),
        )

    def analyze_page(
        self,
        image_path: Path,
        *,
        page_number: int,
        content_profile: str,
        native_markdown: str,
        prompt_version: str,
    ) -> SlideVisionResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=PDF_SYSTEM_INSTRUCTIONS,
            prompt=page_prompt(
                page_number=page_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
            ),
            result_type=SlideVisionResult,
        )

    def review_slide(
        self,
        image_path: Path,
        *,
        slide_number: int,
        content_profile: str,
        native_markdown: str,
        native_elements: list[dict[str, Any]],
        vision: SlideVisionResult,
        reconciliation: SlideReconciliation,
        prompt_version: str,
    ) -> SlideReviewResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=REVIEW_SYSTEM_INSTRUCTIONS,
            prompt=review_prompt(
                slide_number=slide_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                native_elements=native_elements,
                vision=vision,
                reconciliation=reconciliation,
            ),
            result_type=SlideReviewResult,
        )

    def resolve_slide(
        self,
        image_path: Path,
        *,
        slide_number: int,
        content_profile: str,
        native_markdown: str,
        current_markdown: str,
        finding: SlideReviewDecisionFinding,
        question: str,
        answer: str,
        prompt_version: str,
    ) -> SlideResolutionResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=RESOLUTION_SYSTEM_INSTRUCTIONS,
            prompt=resolution_prompt(
                slide_number=slide_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                current_markdown=current_markdown,
                finding=finding,
                question=question,
                answer=answer,
            ),
            result_type=SlideResolutionResult,
        )

    def _analyze(
        self,
        image_path: Path,
        *,
        instructions: str,
        prompt: str,
        result_type: type[BaseModel] = SlideVisionResult,
    ) -> Any:
        image = base64.b64encode(image_path.read_bytes()).decode("ascii")
        try:
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [image],
                    },
                ],
                "format": result_type.model_json_schema(),
                "stream": False,
                "think": False,
                "options": {},
            }
            if self._temperature is not None:
                payload["options"]["temperature"] = self._temperature
            response = self._client.post(
                "/api/chat",
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            return result_type.model_validate(json.loads(content))
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise VisionProviderError(
                f"Ollama vision request failed: {type(exc).__name__}"
            ) from exc
