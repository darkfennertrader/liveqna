from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from openai import OpenAI
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
    DOCX_RESOLUTION_SYSTEM_INSTRUCTIONS,
    DOCX_REVIEW_SYSTEM_INSTRUCTIONS,
    DOCX_SYSTEM_INSTRUCTIONS,
    PDF_RESOLUTION_SYSTEM_INSTRUCTIONS,
    PDF_REVIEW_SYSTEM_INSTRUCTIONS,
    PDF_SYSTEM_INSTRUCTIONS,
    RESOLUTION_SYSTEM_INSTRUCTIONS,
    REVIEW_SYSTEM_INSTRUCTIONS,
    SYSTEM_INSTRUCTIONS,
    XLSX_RESOLUTION_SYSTEM_INSTRUCTIONS,
    XLSX_REVIEW_SYSTEM_INSTRUCTIONS,
    XLSX_SYSTEM_INSTRUCTIONS,
    page_prompt,
    page_resolution_prompt,
    page_review_prompt,
    resolution_prompt,
    review_prompt,
    slide_prompt,
    word_page_prompt,
    word_page_resolution_prompt,
    word_page_review_prompt,
    worksheet_prompt,
    worksheet_resolution_prompt,
    worksheet_review_prompt,
)


class OpenAIVisionProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float | None = None,
        detail: str = "high",
        timeout_seconds: float = 120.0,
        max_retries: int = 1,
        max_output_tokens: int = 4000,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._temperature = temperature
        self._api_key = api_key
        self._detail = detail
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._client = client
        if self._client is None and api_key:
            self._client = OpenAI(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=max_retries,
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
        prompt_version: str,
    ) -> SlideVisionResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=PDF_SYSTEM_INSTRUCTIONS,
            prompt=page_prompt(
                page_number=page_number,
                content_profile=content_profile,
            ),
            result_type=SlideVisionResult,
        )

    def analyze_word_page(
        self,
        image_path: Path,
        *,
        page_number: int,
        content_profile: str,
        prompt_version: str,
    ) -> SlideVisionResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=DOCX_SYSTEM_INSTRUCTIONS,
            prompt=word_page_prompt(
                page_number=page_number,
                content_profile=content_profile,
            ),
            result_type=SlideVisionResult,
        )

    def analyze_worksheet(
        self,
        image_path: Path,
        *,
        worksheet_index: int,
        worksheet_title: str,
        content_profile: str,
        prompt_version: str,
    ) -> SlideVisionResult:
        del prompt_version
        return self._analyze(
            image_path,
            instructions=XLSX_SYSTEM_INSTRUCTIONS,
            prompt=worksheet_prompt(
                worksheet_index=worksheet_index,
                worksheet_title=worksheet_title,
                content_profile=content_profile,
            ),
            result_type=SlideVisionResult,
        )

    def review_page(
        self,
        image_path: Path,
        *,
        page_number: int,
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
            instructions=PDF_REVIEW_SYSTEM_INSTRUCTIONS,
            prompt=page_review_prompt(
                page_number=page_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                native_elements=native_elements,
                vision=vision,
                reconciliation=reconciliation,
            ),
            result_type=SlideReviewResult,
        )

    def review_word_page(
        self,
        image_path: Path,
        *,
        page_number: int,
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
            instructions=DOCX_REVIEW_SYSTEM_INSTRUCTIONS,
            prompt=word_page_review_prompt(
                page_number=page_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                native_elements=native_elements,
                vision=vision,
                reconciliation=reconciliation,
            ),
            result_type=SlideReviewResult,
        )

    def review_worksheet(
        self,
        image_path: Path,
        *,
        worksheet_index: int,
        worksheet_title: str,
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
            instructions=XLSX_REVIEW_SYSTEM_INSTRUCTIONS,
            prompt=worksheet_review_prompt(
                worksheet_index=worksheet_index,
                worksheet_title=worksheet_title,
                content_profile=content_profile,
                native_markdown=native_markdown,
                native_elements=native_elements,
                vision=vision,
                reconciliation=reconciliation,
            ),
            result_type=SlideReviewResult,
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

    def resolve_page(
        self,
        image_path: Path,
        *,
        page_number: int,
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
            instructions=PDF_RESOLUTION_SYSTEM_INSTRUCTIONS,
            prompt=page_resolution_prompt(
                page_number=page_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                current_markdown=current_markdown,
                finding=finding,
                question=question,
                answer=answer,
            ),
            result_type=SlideResolutionResult,
        )

    def resolve_word_page(
        self,
        image_path: Path,
        *,
        page_number: int,
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
            instructions=DOCX_RESOLUTION_SYSTEM_INSTRUCTIONS,
            prompt=word_page_resolution_prompt(
                page_number=page_number,
                content_profile=content_profile,
                native_markdown=native_markdown,
                current_markdown=current_markdown,
                finding=finding,
                question=question,
                answer=answer,
            ),
            result_type=SlideResolutionResult,
        )

    def resolve_worksheet(
        self,
        image_path: Path,
        *,
        worksheet_index: int,
        worksheet_title: str,
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
            instructions=XLSX_RESOLUTION_SYSTEM_INSTRUCTIONS,
            prompt=worksheet_resolution_prompt(
                worksheet_index=worksheet_index,
                worksheet_title=worksheet_title,
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
        if self._client is None:
            if not self._api_key:
                raise VisionProviderError(
                    "OPENAI_API_KEY is required for visual extraction"
                )
            self._client = OpenAI(
                api_key=self._api_key,
                timeout=self._timeout_seconds,
                max_retries=self._max_retries,
            )
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        try:
            request: dict[str, Any] = {
                "model": self.model,
                "instructions": instructions,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": prompt,
                            },
                            {
                                "type": "input_image",
                                "image_url": f"data:image/png;base64,{encoded}",
                                "detail": self._detail,
                            },
                        ],
                    }
                ],
                "text_format": result_type,
                "max_output_tokens": self._max_output_tokens,
                "store": False,
            }
            if self._temperature is not None:
                request["temperature"] = self._temperature
            response = self._client.responses.parse(**request)
        except Exception as exc:
            raise VisionProviderError(
                f"OpenAI vision request failed: {type(exc).__name__}"
            ) from exc
        parsed = response.output_parsed
        if parsed is None:
            raise VisionProviderError("OpenAI returned no parsed vision result")
        return result_type.model_validate(parsed)
