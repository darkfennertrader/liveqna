from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..extraction.models import (
    SlideReconciliation,
    SlideReviewResult,
    SlideVisionResult,
)


class VisionProviderError(RuntimeError):
    """Raised when a vision provider cannot return a valid slide result."""


@runtime_checkable
class VisionProvider(Protocol):
    name: str
    model: str

    def analyze_slide(
        self,
        image_path: Path,
        *,
        slide_number: int,
        content_profile: str,
        native_markdown: str,
        native_elements: list[dict[str, Any]],
        prompt_version: str,
    ) -> SlideVisionResult: ...

    def analyze_page(
        self,
        image_path: Path,
        *,
        page_number: int,
        content_profile: str,
        native_markdown: str,
        prompt_version: str,
    ) -> SlideVisionResult: ...


@runtime_checkable
class SlideReviewProvider(Protocol):
    name: str
    model: str

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
    ) -> SlideReviewResult: ...
