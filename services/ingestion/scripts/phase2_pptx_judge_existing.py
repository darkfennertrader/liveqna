from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.extraction.models import (
    NormalizedSlide,
    SlideReviewProvenance,
)
from app.extraction.pptx_reconcile import reconcile_existing_slide
from app.extraction.pptx_review import decide_slide_review, failed_slide_review
from app.settings import Settings
from app.vision import create_review_provider
from scripts.phase2_pptx_review import _markdown_report, _write_json, _write_text


def add_judge_results(input_directory: Path, output_directory: Path) -> dict:
    if input_directory.resolve() == output_directory.resolve():
        raise ValueError("output_directory must differ from input_directory")
    if output_directory.exists():
        raise ValueError(f"output directory already exists: {output_directory}")
    shutil.copytree(input_directory, output_directory)

    payload = json.loads(
        (input_directory / "review.json").read_text(encoding="utf-8")
    )
    settings = Settings.from_environment()
    provider = create_review_provider(settings)
    errors: dict[int, str] = {}
    completed: dict[int, NormalizedSlide] = {}

    def judge(raw_slide: dict) -> NormalizedSlide:
        slide = reconcile_existing_slide(NormalizedSlide.model_validate(raw_slide))
        if slide.reconciliation is None:
            raise ValueError("slide reconciliation is missing")
        image_path = input_directory / slide.image_path
        started = time.perf_counter()
        result = provider.review_slide(
            image_path,
            slide_number=slide.slide_number,
            content_profile=payload["content_profile"],
            native_markdown=slide.native_markdown,
            native_elements=[
                element.model_dump(mode="json")
                for element in slide.native_elements
            ],
            vision=slide.vision,
            reconciliation=slide.reconciliation,
            prompt_version=settings.review_prompt_version,
        )
        duration_ms = round((time.perf_counter() - started) * 1000)
        response_hash = hashlib.sha256(
            json.dumps(
                result.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        decision = decide_slide_review(
            vision=slide.vision,
            reconciliation=slide.reconciliation,
            judge_result=result,
            judge_provenance=SlideReviewProvenance(
                provider=provider.name,
                model=provider.model,
                prompt_version=settings.review_prompt_version,
                response_hash=response_hash,
                duration_ms=duration_ms,
            ),
        )
        return slide.model_copy(update={"review": decision})

    slides_by_number = {
        raw_slide["slide_number"]: raw_slide for raw_slide in payload["slides"]
    }
    workers = min(settings.vision_max_concurrency, len(slides_by_number))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(judge, raw_slide): number
            for number, raw_slide in slides_by_number.items()
        }
        for future in as_completed(futures):
            number = futures[future]
            try:
                completed[number] = future.result()
            except Exception as exc:
                errors[number] = f"{type(exc).__name__}: {exc}"
                original = reconcile_existing_slide(
                    NormalizedSlide.model_validate(slides_by_number[number])
                )
                completed[number] = original.model_copy(
                    update={"review": failed_slide_review(errors[number])}
                )

    payload.update(
        {
            "review_provider": provider.name,
            "review_model": provider.model,
            "review_prompt_version": settings.review_prompt_version,
            "slides": [
                completed[number].model_dump(mode="json")
                for number in sorted(completed)
            ],
            "errors": {str(number): errors[number] for number in sorted(errors)},
        }
    )
    _write_json(output_directory / "review.json", payload)
    _write_text(output_directory / "review.md", _markdown_report(payload))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add only the second-call judge to an existing PPTX visual review"
    )
    parser.add_argument("input_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    payload = add_judge_results(args.input_directory, args.output_directory)
    print(
        json.dumps(
            {
                "output": str(args.output_directory / "review.json"),
                "reviewed_slides": len(payload["slides"]),
                "errors": payload["errors"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
