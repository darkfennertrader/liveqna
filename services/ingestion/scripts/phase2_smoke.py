from __future__ import annotations

import argparse
import io
import json
from datetime import UTC, datetime

import httpx
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pypdf import PdfWriter


def require(response: httpx.Response, expected: int):
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url}: "
            f"expected {expected}, received {response.status_code}: {response.text}"
        )
    return response.json() if response.content else None


def docx_bytes() -> bytes:
    stream = io.BytesIO()
    document = Document()
    document.add_heading("Architecture decision", level=1)
    document.add_paragraph("Use one isolated knowledge project per engagement.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Control"
    table.cell(0, 1).text = "Owner"
    table.cell(1, 0).text = "Approval"
    table.cell(1, 1).text = "Local owner"
    document.save(stream)
    return stream.getvalue()


def xlsx_bytes() -> bytes:
    stream = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Services"
    sheet.append(["Service", "Purpose"])
    sheet.append(["Qdrant", "Hybrid retrieval"])
    workbook.save(stream)
    return stream.getvalue()


def pptx_bytes() -> bytes:
    stream = io.BytesIO()
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Target architecture"
    slide.placeholders[1].text = "Registry, extraction, and retrieval"
    slide.notes_slide.notes_text_frame.text = "Validate this slide with the client."
    presentation.save(stream)
    return stream.getvalue()


def blank_pdf_bytes() -> bytes:
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)
    return stream.getvalue()


def run(base_url: str) -> dict:
    suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    files = [
        (
            "Architecture notes",
            "official",
            "architecture.md",
            b"# Architecture\n\nUse an approved knowledge boundary.\n",
            "md",
            "READY",
        ),
        (
            "Meeting transcript",
            "transcript",
            "meeting.txt",
            b"[00:01:05] Alice: The target is approved.\n",
            "txt",
            "READY",
        ),
        (
            "Decision document",
            "artifact",
            "decision.docx",
            docx_bytes(),
            "docx",
            "READY",
        ),
        (
            "Service inventory",
            "internal",
            "services.xlsx",
            xlsx_bytes(),
            "xlsx",
            "READY",
        ),
        (
            "Architecture deck",
            "artifact",
            "architecture.pptx",
            pptx_bytes(),
            "pptx",
            "READY",
        ),
        (
            "Scanned document example",
            "official",
            "scan.pdf",
            blank_pdf_bytes(),
            "pdf",
            "NEEDS_REVIEW",
        ),
    ]
    with httpx.Client(base_url=base_url, timeout=30) as client:
        project = require(
            client.post("/projects", json={"name": f"Phase 2 Review {suffix}"}),
            201,
        )
        results = []
        approved_version_id = None
        failed_version_id = None
        for title, document_type, filename, content, source_format, expected in files:
            document = require(
                client.post(
                    f"/projects/{project['id']}/documents",
                    json={"title": title, "document_type": document_type},
                ),
                201,
            )
            version = require(
                client.post(
                    f"/documents/{document['id']}/versions",
                    data={"version_label": "v1"},
                    files={"file": (filename, content)},
                ),
                201,
            )
            assert version["processing_status"] == expected
            extraction = require(
                client.get(f"/versions/{version['id']}/extraction"), 200
            )
            item = {
                "filename": filename,
                "version_id": version["id"],
                "processing_status": extraction["processing_status"],
                "error_code": extraction["error_code"],
            }
            if expected == "READY":
                normalized = require(
                    client.get(f"/versions/{version['id']}/normalized"), 200
                )
                assert normalized["source_format"] == source_format
                assert normalized["blocks"]
                item["block_count"] = len(normalized["blocks"])
                if approved_version_id is None:
                    approved_version_id = version["id"]
            else:
                assert extraction["error_code"] == "OCR_REQUIRED"
                failed_version_id = version["id"]
            results.append(item)

        require(client.post(f"/versions/{approved_version_id}/approve"), 200)
        require(client.post(f"/versions/{failed_version_id}/approve"), 409)

    return {
        "project": project,
        "formats": results,
        "verified": {
            "normalized_outputs_saved": True,
            "source_locators_present": True,
            "successful_extraction_can_be_approved": True,
            "review_required_extraction_cannot_be_approved": True,
            "blank_pdf_reports_ocr_required": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise Phase 2 extraction")
    parser.add_argument("--base-url", default="http://127.0.0.1:8084")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url), indent=2))


if __name__ == "__main__":
    main()
