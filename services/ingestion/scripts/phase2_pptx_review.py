from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pptx import Presentation

from app.extraction.common import DocumentBuilder
from app.extraction.pptx import _enrich_slide, _extract_native_slides
from app.extraction.pptx_renderer import LibreOfficePptxRenderer
from app.settings import Settings
from app.vision import create_review_provider, create_vision_provider


DEFAULT_SLIDES = "5,6,14,22,24"


def parse_slides(value: str) -> list[int]:
    try:
        slides = sorted({int(item.strip()) for item in value.split(",")})
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "slides must be a comma-separated list of integers"
        ) from exc
    if not slides or any(number < 1 for number in slides):
        raise argparse.ArgumentTypeError("slide numbers must be positive")
    return slides


def run_review(
    source: Path,
    output_directory: Path,
    slide_numbers: list[int],
    content_profile: str,
) -> dict:
    settings = Settings.from_environment()
    output_directory.mkdir(parents=True, exist_ok=True)
    working_source = output_directory / "source.pptx"
    if source.resolve() != working_source.resolve():
        shutil.copy2(source, working_source)

    content_hash = hashlib.sha256(working_source.read_bytes()).hexdigest()
    presentation = Presentation(working_source)
    builder = DocumentBuilder(source.name, "pptx", content_hash)
    native_slides = _extract_native_slides(presentation, builder)
    available = {slide.slide_number: slide for slide in native_slides}
    missing = [number for number in slide_numbers if number not in available]
    if missing:
        raise ValueError(f"slide numbers are outside the deck: {missing}")

    renderer = LibreOfficePptxRenderer(
        soffice_binary=settings.pptx_soffice_binary,
        pdftoppm_binary=settings.pptx_pdftoppm_binary,
        dpi=settings.pptx_render_dpi,
        timeout_seconds=settings.pptx_render_timeout_seconds,
    )
    rendered = {
        slide.slide_number: slide
        for slide in renderer.render(working_source, len(native_slides))
    }
    provider = create_vision_provider(settings)
    review_provider = create_review_provider(settings)

    results = {}
    errors = {}
    workers = min(settings.vision_max_concurrency, len(slide_numbers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _enrich_slide,
                available[number],
                rendered[number],
                content_profile,
                provider,
                review_provider,
                settings.vision_prompt_version,
                settings.review_prompt_version,
                renderer.version,
            ): number
            for number in slide_numbers
        }
        for future in as_completed(futures):
            number = futures[future]
            try:
                results[number] = future.result()
            except Exception as exc:
                errors[number] = f"{type(exc).__name__}: {exc}"

    payload = {
        "source": str(source),
        "content_hash": content_hash,
        "content_profile": content_profile,
        "selected_slides": slide_numbers,
        "provider": provider.name,
        "model": provider.model,
        "prompt_version": settings.vision_prompt_version,
        "review_provider": review_provider.name,
        "review_model": review_provider.model,
        "review_prompt_version": settings.review_prompt_version,
        "slides": [
            results[number].model_dump(mode="json")
            for number in sorted(results)
        ],
        "errors": {str(number): errors[number] for number in sorted(errors)},
    }
    _write_json(output_directory / "review.json", payload)
    _write_text(output_directory / "review.md", _markdown_report(payload))
    return payload


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _markdown_report(payload: dict) -> str:
    lines = [
        "# Phase 2 PPTX hybrid extraction review",
        "",
        f"- Source: `{payload['source']}`",
        f"- Provider: `{payload['provider']}`",
        f"- Model: `{payload['model']}`",
        f"- Review provider: `{payload['review_provider']}`",
        f"- Review model: `{payload['review_model']}`",
        f"- Selected slides: {', '.join(map(str, payload['selected_slides']))}",
        "",
    ]
    for slide in payload["slides"]:
        number = slide["slide_number"]
        lines.extend(
            [
                f"## Slide {number}: {slide.get('title') or '(untitled)'}",
                "",
                f"![Rendered slide {number}]({slide['image_path']})",
                "",
                "### Native extraction",
                "",
                slide["native_markdown"] or "_(No native visible text.)_",
                "",
                "### Vision extraction",
                "",
                slide["vision"]["transcription_markdown"]
                or "_(No visual transcription.)_",
                "",
                "### Visual evidence",
                "",
                slide["vision"]["visual_description"]
                or "_(No visual description.)_",
                "",
                "### Labels",
                "",
                ", ".join(slide["vision"]["labels"]) or "_(None.)_",
                "",
                "### Discrepancies and uncertainties",
                "",
            ]
        )
        discrepancies = slide["vision"]["discrepancies"]
        uncertainties = slide["vision"]["uncertainties"]
        if discrepancies:
            lines.extend(
                f"- {'MATERIAL' if item['material'] else 'Non-material'}: "
                f"{item['description']}"
                for item in discrepancies
            )
        if uncertainties:
            lines.extend(f"- Uncertainty: {item}" for item in uncertainties)
        if not discrepancies and not uncertainties:
            lines.append("- None reported.")
        review = slide.get("review")
        lines.extend(["", "### Independent review decision", ""])
        if review is None:
            lines.append("- REVIEW_REQUIRED: independent review was not run.")
        else:
            lines.extend(
                [
                    f"- Status: **{review['status']}**",
                    f"- Severity: **{review['severity']}**",
                ]
            )
            lines.extend(
                f"- `{item['category']}` ({item['source']}): "
                f"{item['description']}"
                for item in review["findings"]
            )
        lines.extend(
            [
                "",
                "### Combined retrieval Markdown",
                "",
                slide["combined_markdown"],
                "",
            ]
        )
    if payload["errors"]:
        lines.extend(["## Errors", ""])
        lines.extend(
            f"- Slide {number}: {message}"
            for number, message in payload["errors"].items()
        )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run paid vision review on selected PPTX slides"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--slides", type=parse_slides, default=parse_slides(DEFAULT_SLIDES))
    parser.add_argument("--content-profile", default="general_presentation")
    args = parser.parse_args()
    payload = run_review(
        args.source,
        args.output_directory,
        args.slides,
        args.content_profile,
    )
    print(
        json.dumps(
            {
                "review": str(args.output_directory / "review.md"),
                "completed_slides": len(payload["slides"]),
                "errors": payload["errors"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
