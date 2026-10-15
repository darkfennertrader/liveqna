from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from app.extraction.errors import ExtractionFailure
from app.extraction.pdf_renderer import PyMuPdfRenderer
from app.extraction.pipeline import ExtractionPipeline
from app.settings import Settings
from app.vision import create_review_provider, create_vision_provider


def run_review(
    source: Path,
    output_directory: Path,
    *,
    document_type: str,
    content_profile: str,
) -> dict:
    settings = Settings.from_environment()
    output_directory.mkdir(parents=True, exist_ok=True)
    working_source = output_directory / "source.pdf"
    if source.resolve() != working_source.resolve():
        shutil.copy2(source, working_source)

    content_hash = hashlib.sha256(working_source.read_bytes()).hexdigest()
    provider = create_vision_provider(settings)
    pipeline = ExtractionPipeline(
        pdf_renderer=PyMuPdfRenderer(dpi=settings.pdf_render_dpi),
        vision_provider=provider,
        review_provider=create_review_provider(settings),
        pdf_vision_prompt_version=settings.pdf_vision_prompt_version,
        pdf_review_prompt_version=settings.pdf_review_prompt_version,
        vision_max_concurrency=settings.vision_max_concurrency,
    )
    error = None
    try:
        normalized = pipeline.extract(
            working_source,
            source.name,
            document_type,
            content_hash,
            content_profile,
        )
    except ExtractionFailure as exc:
        if exc.partial_document is None:
            raise
        normalized = exc.partial_document
        error = {"code": exc.code, "message": str(exc)}

    payload = {
        "source": str(source),
        "content_hash": content_hash,
        "document_type": document_type,
        "content_profile": content_profile,
        "provider": provider.name,
        "model": provider.model,
        "prompt_version": settings.pdf_vision_prompt_version,
        "normalized": normalized.model_dump(mode="json"),
        "error": error,
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
    normalized = payload["normalized"]
    metadata = normalized.get("transcript_metadata")
    lines = [
        "# Phase 2 PDF extraction review",
        "",
        f"- Source: `{payload['source']}`",
        f"- Document type: `{payload['document_type']}`",
        f"- Content profile: `{payload['content_profile']}`",
        f"- Native blocks: {len(normalized['blocks'])}",
        f"- Visually enriched pages: {len(normalized['pages'])}",
        f"- Quality: `{normalized['quality']['status']}`",
        "",
    ]
    if metadata is not None:
        lines.extend(
            [
                "## Transcript metadata",
                "",
                f"- Video: `{metadata.get('video_name') or '(not provided)'}`",
                "- Overall range: "
                f"`{metadata.get('time_range_start') or '(not provided)'}` to "
                f"`{metadata.get('time_range_end') or '(not provided)'}`",
                f"- Duration seconds: `{metadata.get('duration_seconds')}`",
                "- Speaker and per-turn timestamps: not inferred",
                "",
            ]
        )
    for page in normalized["pages"]:
        number = page["page_number"]
        lines.extend(
            [
                f"## Visually enriched page {number}",
                "",
                "- Selection reasons: "
                + ", ".join(page["selection_reasons"]),
                f"- Native characters: {page['native_text_character_count']}",
                f"- Largest image pixels: {page['max_image_pixels']}",
                f"- Vector drawing operations: {page['vector_operation_count']}",
                "",
                f"![Rendered page {number}]({page['image_path']})",
                "",
                "### Native extraction",
                "",
                page["native_markdown"] or "_(No native visible text.)_",
                "",
                "### Vision extraction",
                "",
                page["vision"]["transcription_markdown"]
                or "_(No visual transcription.)_",
                "",
                "### Visual evidence",
                "",
                page["vision"]["visual_description"]
                or "_(No visual description.)_",
                "",
                "### Discrepancies and uncertainties",
                "",
            ]
        )
        discrepancies = page["vision"]["discrepancies"]
        uncertainties = page["vision"]["uncertainties"]
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
        lines.extend(
            [
                "",
                "### Combined retrieval Markdown",
                "",
                page["combined_markdown"],
                "",
            ]
        )
    if payload["error"] is not None:
        lines.extend(
            [
                "## Review-required result",
                "",
                f"- Code: `{payload['error']['code']}`",
                f"- Detail: {payload['error']['message']}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run repeatable PDF transcript or hybrid visual review"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--document-type",
        choices=["official", "internal", "transcript", "artifact"],
        default="official",
    )
    parser.add_argument("--content-profile", default="general_document")
    args = parser.parse_args()
    payload = run_review(
        args.source,
        args.output_directory,
        document_type=args.document_type,
        content_profile=args.content_profile,
    )
    print(
        json.dumps(
            {
                "review": str(args.output_directory / "review.md"),
                "enriched_pages": len(payload["normalized"]["pages"]),
                "error": payload["error"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
