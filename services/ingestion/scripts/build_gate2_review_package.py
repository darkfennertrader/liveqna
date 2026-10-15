from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from shutil import rmtree
from zoneinfo import ZoneInfo

from app.extraction.errors import ExtractionFailure
from app.extraction.models import NormalizedDocument, NormalizedSlide
from app.extraction.pptx import extract_native_slide_elements
from app.extraction.pdf_renderer import PyMuPdfRenderer
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.pptx_link import link_slides
from app.extraction.pptx_report import (
    PPTX_LLM_REPORT_FILENAME,
    PPTX_REVIEW_REPORT_FILENAME,
    build_pptx_llm_report,
    build_pptx_review_report,
)
from app.extraction.pptx_reconcile import reconcile_existing_slide
from app.extraction.pptx_renderer import LibreOfficePptxRenderer
from app.settings import Settings


SUPPORTED_SUFFIXES = {".docx", ".md", ".markdown", ".pdf", ".pptx", ".txt", ".xlsx"}
DEFAULT_GATE2_TIMEZONE = "Europe/Rome"
PRINT_CSS = Path(__file__).resolve().parents[1] / "docs" / "gate-2-print.css"
SEVERITY_RANK = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _document_directory(
    source: Path, *, transcript_filename: str
) -> Path:
    suffix = source.suffix.lower()
    if source.name == transcript_filename:
        category = "transcript"
    elif suffix in {".md", ".markdown", ".txt"}:
        category = "markdown-text"
    else:
        category = suffix.removeprefix(".")
    return Path(category) / _slug(source.stem)


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _copy_evidence(
    source_directory: Path,
    destination_directory: Path,
    package_directory: Path,
) -> list[str]:
    copied: list[str] = []
    if not source_directory.exists():
        return copied
    destination_directory.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_directory.glob("*.png")):
        destination = destination_directory / source.name
        shutil.copy2(source, destination)
        copied.append(destination.relative_to(package_directory).as_posix())
    return copied


def _merge_pptx_sample(
    normalized: dict,
    source_path: Path,
    review_path: Path,
    document_directory: Path,
    package_directory: Path,
) -> tuple[list[int], list[str]]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review["content_hash"] != normalized["content_hash"]:
        raise ValueError("PPTX review content hash does not match the sample file")
    if review.get("errors"):
        raise ValueError("PPTX review contains failed slide extractions")

    selected_numbers = set(review["selected_slides"])
    normalized["blocks"] = [
        block
        for block in normalized["blocks"]
        if block["source"].get("slide") in selected_numbers
    ]
    normalized["quality"]["block_count"] = len(normalized["blocks"])
    normalized["quality"]["text_character_count"] = sum(
        len(block["text"]) for block in normalized["blocks"]
    )

    evidence_directory = document_directory / "rendered-slides"
    evidence_directory.mkdir(parents=True, exist_ok=True)
    slides = review["slides"]
    evidence: list[str] = []
    for slide in slides:
        source_image = review_path.parent / slide["image_path"]
        destination = evidence_directory / source_image.name
        shutil.copy2(source_image, destination)
        slide["image_path"] = destination.relative_to(
            document_directory
        ).as_posix()
        evidence.append(
            destination.relative_to(package_directory).as_posix()
        )

    native_elements = extract_native_slide_elements(
        source_path,
        normalized["source_filename"],
        normalized["content_hash"],
    )
    upgraded_slides: list[dict] = []
    for slide in slides:
        slide_number = slide["slide_number"]
        slide["native_elements"] = [
            element.model_dump(mode="json")
            for element in native_elements[slide_number]
        ]
        upgraded_slides.append(
            reconcile_existing_slide(
                NormalizedSlide.model_validate(slide)
            ).model_dump(mode="json")
        )
    normalized["slides"] = [
        slide.model_dump(mode="json")
        for slide in link_slides(
            [NormalizedSlide.model_validate(slide) for slide in upgraded_slides]
        )
    ]
    normalized["schema_version"] = "1.6"
    normalized["quality"]["status"] = "WARNING"
    normalized["quality"]["warnings"].append(
        "Gate 2 contains matching native and visual output only for slides "
        + ", ".join(str(number) for number in review["selected_slides"])
        + "; production ingestion still processes the complete presentation."
    )
    return review["selected_slides"], evidence


def _pipeline(settings: Settings) -> ExtractionPipeline:
    return ExtractionPipeline(
        pdf_renderer=PyMuPdfRenderer(dpi=settings.pdf_render_dpi),
        pptx_renderer=LibreOfficePptxRenderer(
            soffice_binary=settings.pptx_soffice_binary,
            pdftoppm_binary=settings.pptx_pdftoppm_binary,
            dpi=settings.pptx_render_dpi,
            timeout_seconds=settings.pptx_render_timeout_seconds,
        ),
        vision_provider=None,
        pdf_vision_prompt_version=settings.pdf_vision_prompt_version,
        vision_prompt_version=settings.vision_prompt_version,
        vision_max_concurrency=settings.vision_max_concurrency,
    )


def _write_pptx_review_views(
    document_directory: Path,
    package_directory: Path,
    normalized: dict,
) -> dict[str, str]:
    base = {
        "source_filename": normalized["source_filename"],
        "content_hash": normalized["content_hash"],
        "schema_version": normalized["schema_version"],
    }
    views = {
        "native_elements": (
            "native-elements.json",
            {
                **base,
                "slides": [
                    {
                        "slide_number": slide["slide_number"],
                        "title": slide["title"],
                        "native_markdown": slide["native_markdown"],
                        "speaker_notes": slide["speaker_notes"],
                        "native_elements": slide["native_elements"],
                    }
                    for slide in normalized["slides"]
                ],
            },
        ),
        "visual_elements": (
            "visual-elements.json",
            {
                **base,
                "slides": [
                    {
                        "slide_number": slide["slide_number"],
                        "title": slide["title"],
                        "image_path": slide["image_path"],
                        "image_hash": slide["image_hash"],
                        "vision": slide["vision"],
                        "vision_provenance": slide["vision_provenance"],
                    }
                    for slide in normalized["slides"]
                ],
            },
        ),
        "reconciliation": (
            "reconciliation.json",
            {
                **base,
                "slides": [
                    {
                        "slide_number": slide["slide_number"],
                        "title": slide["title"],
                        "reconciliation": slide["reconciliation"],
                        "retrieval_markdown": slide["combined_markdown"],
                    }
                    for slide in normalized["slides"]
                ],
            },
        ),
        "review_decisions": (
            "review-decisions.json",
            {
                **base,
                "slides": [
                    {
                        "slide_number": slide["slide_number"],
                        "title": slide["title"],
                        "review": slide.get("review"),
                    }
                    for slide in normalized["slides"]
                ],
            },
        ),
    }
    paths: dict[str, str] = {}
    for key, (filename, payload) in views.items():
        destination = document_directory / filename
        _write_json(destination, payload)
        paths[key] = destination.relative_to(package_directory).as_posix()
    return paths


def _build_run(
    source_directory: Path,
    output_directory: Path,
    *,
    settings: Settings,
    run_id: str,
    created_at: str,
    timezone_name: str,
    transcript_filename: str,
    pptx_review: Path,
    architecture_evidence: Path,
    financial_evidence: Path,
) -> dict:
    pipeline = _pipeline(settings)
    output_directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PRINT_CSS, output_directory / PRINT_CSS.name)

    sources = sorted(
        path
        for path in source_directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    entries: list[dict] = []

    for source in sources:
        relative_document_directory = _document_directory(
            source, transcript_filename=transcript_filename
        )
        document_directory = output_directory / relative_document_directory
        document_directory.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        document_type = (
            "transcript" if source.name == transcript_filename else "official"
        )
        content_profile = (
            "general_presentation"
            if source.suffix.lower() == ".pptx"
            else "general_document"
        )
        extraction_error: dict | None = None
        try:
            document = pipeline.extract(
                source,
                source.name,
                document_type,
                content_hash,
                content_profile,
            )
        except ExtractionFailure as exc:
            if exc.partial_document is None:
                raise
            document = exc.partial_document
            extraction_error = {"code": exc.code, "message": str(exc)}

        normalized = document.model_dump(mode="json")
        evidence: list[str] = []
        reviewed_units: list[int] = []
        llm_report: str | None = None
        pptx_review_report: str | None = None
        review_artifacts: dict[str, str] = {}
        review_status = "PASS"
        review_severity = "NONE"
        units_to_review: list[int] = []
        review_unit_label = "Document"
        review_note = "Native extraction is complete."

        if source.suffix.lower() == ".pptx":
            reviewed_units, evidence = _merge_pptx_sample(
                normalized,
                source,
                pptx_review,
                document_directory,
                output_directory,
            )
            extraction_error = None
            flagged_slides = [
                slide
                for slide in normalized["slides"]
                if slide.get("review") is None
                or slide["review"]["status"] == "REVIEW_REQUIRED"
            ]
            units_to_review = [slide["slide_number"] for slide in flagged_slides]
            review_unit_label = "Slide"
            review_status = (
                "NEEDS_HUMAN_REVIEW" if flagged_slides else "PASS"
            )
            review_severity = max(
                (
                    slide["review"]["severity"]
                    if slide.get("review") is not None
                    else "MEDIUM"
                    for slide in flagged_slides
                ),
                key=lambda value: SEVERITY_RANK[value],
                default="NONE",
            )
            review_note = (
                "Review only flagged slides: "
                + ", ".join(str(number) for number in units_to_review)
                if flagged_slides
                else "All evaluated PPTX slides passed the independent review."
            )
        elif source.name == "lighthouse-consolidated-architecture.pdf":
            evidence = _copy_evidence(
                architecture_evidence,
                document_directory / "rendered-pages",
                output_directory,
            )
            reviewed_units = [2, 4, 6, 8, 10]
            units_to_review = list(reviewed_units)
            review_unit_label = "Page"
            review_status = "NEEDS_HUMAN_REVIEW"
            review_severity = "UNASSESSED"
            review_note = "Inspect the five rendered diagram pages; vision normalization is pending."
        elif source.name.startswith("260720_Aegon Lighthouse"):
            evidence = _copy_evidence(
                financial_evidence,
                document_directory / "rendered-pages",
                output_directory,
            )
            reviewed_units = [17, 21]
            units_to_review = list(reviewed_units)
            review_unit_label = "Page"
            review_status = "NEEDS_HUMAN_REVIEW"
            review_severity = "UNASSESSED"
            review_note = "Inspect the two rendered chart pages; vision normalization is pending."
        elif extraction_error is not None:
            review_status = "NEEDS_HUMAN_REVIEW"
            review_severity = "UNASSESSED"
            review_note = extraction_error["message"]

        if source.suffix.lower() == ".pptx":
            report_path = document_directory / PPTX_LLM_REPORT_FILENAME
            llm_report = report_path.relative_to(output_directory).as_posix()
            _write_text(
                report_path,
                build_pptx_llm_report(
                    NormalizedDocument.model_validate(normalized)
                ),
            )
            review_report_path = document_directory / PPTX_REVIEW_REPORT_FILENAME
            pptx_review_report = review_report_path.relative_to(
                output_directory
            ).as_posix()
            _write_text(
                review_report_path,
                build_pptx_review_report(
                    NormalizedDocument.model_validate(normalized)
                ),
            )
            review_artifacts = _write_pptx_review_views(
                document_directory,
                output_directory,
                normalized,
            )

        normalized_path = document_directory / "normalized.json"
        _write_json(normalized_path, normalized)
        normalized_filename = normalized_path.relative_to(
            output_directory
        ).as_posix()
        entries.append(
            {
                "source_filename": source.name,
                "source_path": str(source),
                "document_directory": relative_document_directory.as_posix(),
                "normalized_json": normalized_filename,
                "review_status": review_status,
                "review_severity": review_severity,
                "units_to_review": units_to_review,
                "review_unit_label": review_unit_label,
                "review_note": review_note,
                "schema_version": normalized["schema_version"],
                "block_count": len(normalized["blocks"]),
                "page_count": len(normalized["pages"]),
                "slide_count": len(normalized["slides"]),
                "reviewed_units": reviewed_units,
                "evidence": evidence,
                "llm_report": llm_report,
                "pptx_review_report": pptx_review_report,
                "review_artifacts": review_artifacts,
                "extraction_error": extraction_error,
            }
        )

    manifest = {
        "gate": "Phase 2 extraction fidelity",
        "run_id": run_id,
        "created_at": created_at,
        "timezone": timezone_name,
        "extractor_version": pipeline.version,
        "overall_status": (
            "OPEN"
            if any(entry["review_status"] != "PASS" for entry in entries)
            else "PASS"
        ),
        "source_directory": str(source_directory),
        "document_count": len(entries),
        "documents": entries,
    }
    _write_json(output_directory / "manifest.json", manifest)
    _write_text(output_directory / "GATE-2-REVIEW.md", _review_markdown(manifest))
    return manifest


def _next_run_id(runs_directory: Path, timestamp: datetime) -> str:
    base = timestamp.strftime("%Y-%m-%d_%H-%M-%S%z")
    candidate = base
    suffix = 2
    while (runs_directory / candidate).exists() or (
        runs_directory / f".{candidate}.building"
    ).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def build_package(
    source_directory: Path,
    runs_directory: Path,
    *,
    transcript_filename: str,
    pptx_review: Path,
    architecture_evidence: Path,
    financial_evidence: Path,
) -> dict:
    settings = Settings.from_environment()
    timezone_name = os.environ.get(
        "INGESTION_GATE2_TIMEZONE", DEFAULT_GATE2_TIMEZONE
    )
    timezone = ZoneInfo(timezone_name)
    timestamp = datetime.now(timezone)
    created_at = timestamp.isoformat(timespec="seconds")

    runs_directory.mkdir(parents=True, exist_ok=True)
    run_id = _next_run_id(runs_directory, timestamp)
    staging_directory = runs_directory / f".{run_id}.building"
    final_directory = runs_directory / run_id

    staging_directory.mkdir(parents=True, exist_ok=False)
    try:
        manifest = _build_run(
            source_directory,
            staging_directory,
            settings=settings,
            run_id=run_id,
            created_at=created_at,
            timezone_name=timezone_name,
            transcript_filename=transcript_filename,
            pptx_review=pptx_review,
            architecture_evidence=architecture_evidence,
            financial_evidence=financial_evidence,
        )
        os.replace(staging_directory, final_directory)
    except Exception:
        rmtree(staging_directory, ignore_errors=True)
        raise

    latest = {
        "latest_run_id": run_id,
        "created_at": created_at,
        "run_directory": run_id,
        "manifest": f"{run_id}/manifest.json",
        "review": f"{run_id}/GATE-2-REVIEW.md",
    }
    _write_json(runs_directory / "latest.json", latest)
    return manifest


def _review_markdown(manifest: dict) -> str:
    lines = [
        "# Gate 2 review",
        "",
        "| Source | System result | Severity | What to check | Detailed report | Human decision |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in manifest["documents"]:
        detailed_path = entry["llm_report"] or entry["normalized_json"]
        detailed_label = (
            "PPTX LLM slide report"
            if entry["llm_report"]
            else "normalized.json"
        )
        units = entry.get("units_to_review", [])
        unit_label = entry.get("review_unit_label", "Document")
        what_to_check = (
            f"{unit_label}{'' if len(units) == 1 else 's'} "
            + ", ".join(str(number) for number in units)
            if units
            else "—"
        )
        human_decision = (
            "PENDING"
            if entry["review_status"] == "NEEDS_HUMAN_REVIEW"
            else "NOT REQUIRED"
        )
        displayed_status = (
            "HUMAN REVIEW REQUIRED"
            if entry["review_status"] == "NEEDS_HUMAN_REVIEW"
            else entry["review_status"]
        )
        lines.append(
            f"| [`{entry['source_filename']}`](<{entry['source_path']}>) | "
            f"**{displayed_status}** | "
            f"**{entry.get('review_severity', 'UNASSESSED')}** | "
            f"**{what_to_check}** | "
            f"[{detailed_label}](<{detailed_path}>) | "
            f"**{human_decision}** |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build one immutable Gate 2 inspection run"
    )
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("runs_directory", type=Path)
    parser.add_argument("--transcript-filename", required=True)
    parser.add_argument("--pptx-review", type=Path, required=True)
    parser.add_argument("--architecture-evidence", type=Path, required=True)
    parser.add_argument("--financial-evidence", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_package(
        args.source_directory,
        args.runs_directory,
        transcript_filename=args.transcript_filename,
        pptx_review=args.pptx_review,
        architecture_evidence=args.architecture_evidence,
        financial_evidence=args.financial_evidence,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
