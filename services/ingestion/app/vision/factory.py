from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SlideResolutionProvider, SlideReviewProvider, VisionProvider
from .ollama import OllamaVisionProvider
from .openai import OpenAIVisionProvider

if TYPE_CHECKING:
    from ..settings import Settings


def create_vision_provider(settings: Settings) -> VisionProvider:
    return _create_provider(
        provider=settings.vision_provider,
        model=settings.vision_model,
        temperature=settings.vision_temperature,
        detail=settings.vision_detail,
        timeout_seconds=settings.vision_timeout_seconds,
        max_retries=settings.vision_max_retries,
        max_output_tokens=settings.vision_max_output_tokens,
        settings=settings,
    )


def create_review_provider(settings: Settings) -> SlideReviewProvider:
    return _create_provider(
        provider=settings.review_provider,
        model=settings.review_model,
        temperature=settings.review_temperature,
        detail=settings.review_detail,
        timeout_seconds=settings.review_timeout_seconds,
        max_retries=settings.review_max_retries,
        max_output_tokens=settings.review_max_output_tokens,
        settings=settings,
    )


def create_resolution_provider(settings: Settings) -> SlideResolutionProvider:
    return _create_provider(
        provider=settings.resolver_provider,
        model=settings.resolver_model,
        temperature=settings.resolver_temperature,
        detail=settings.resolver_detail,
        timeout_seconds=settings.resolver_timeout_seconds,
        max_retries=settings.resolver_max_retries,
        max_output_tokens=settings.resolver_max_output_tokens,
        settings=settings,
    )


def _create_provider(
    *,
    provider: str,
    model: str,
    temperature: float | None,
    detail: str,
    timeout_seconds: float,
    max_retries: int,
    max_output_tokens: int,
    settings: Settings,
):
    if provider == "openai":
        return OpenAIVisionProvider(
            api_key=settings.openai_api_key,
            model=model,
            temperature=temperature,
            detail=detail,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_output_tokens=max_output_tokens,
        )
    if provider == "ollama":
        return OllamaVisionProvider(
            model=model,
            temperature=temperature,
            base_url=settings.ollama_base_url,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"unsupported ingestion multimodal provider: {provider}")
