from __future__ import annotations

from pathlib import Path

from .docx import extract_docx
from .errors import ExtractionFailure
from .models import NormalizedDocument
from .pdf import extract_pdf
from .pdf_renderer import PdfRenderer
from .pptx import extract_pptx
from .pptx_renderer import PptxRenderer
from .text import extract_text_document
from .xlsx import extract_xlsx
from .xlsx_renderer import XlsxRenderer
from .xlsx_validation import XlsxPackageLimits
from ..vision.base import SlideReviewProvider, VisionProvider
from ..vision.prompt import (
    PDF_PROMPT_VERSION,
    PDF_REVIEW_PROMPT_VERSION,
    XLSX_PROMPT_VERSION,
    XLSX_REVIEW_PROMPT_VERSION,
    REVIEW_PROMPT_VERSION,
)


EXTRACTOR_VERSION = "normalized-document-v8-xlsx-review"


class ExtractionPipeline:
    version = EXTRACTOR_VERSION

    def __init__(
        self,
        *,
        pdf_renderer: PdfRenderer | None = None,
        pptx_renderer: PptxRenderer | None = None,
        xlsx_renderer: XlsxRenderer | None = None,
        vision_provider: VisionProvider | None = None,
        review_provider: SlideReviewProvider | None = None,
        pdf_vision_prompt_version: str = PDF_PROMPT_VERSION,
        pdf_review_prompt_version: str = PDF_REVIEW_PROMPT_VERSION,
        vision_prompt_version: str = "pptx-slide-v4",
        review_prompt_version: str = REVIEW_PROMPT_VERSION,
        xlsx_vision_prompt_version: str = XLSX_PROMPT_VERSION,
        xlsx_review_prompt_version: str = XLSX_REVIEW_PROMPT_VERSION,
        xlsx_package_limits: XlsxPackageLimits | None = None,
        xlsx_visual_mode: str = "selective",
        vision_max_concurrency: int = 4,
    ) -> None:
        self.pdf_renderer = pdf_renderer
        self.pptx_renderer = pptx_renderer
        self.xlsx_renderer = xlsx_renderer
        self.vision_provider = vision_provider
        self.review_provider = review_provider
        self.pdf_vision_prompt_version = pdf_vision_prompt_version
        self.pdf_review_prompt_version = pdf_review_prompt_version
        self.vision_prompt_version = vision_prompt_version
        self.review_prompt_version = review_prompt_version
        self.xlsx_vision_prompt_version = xlsx_vision_prompt_version
        self.xlsx_review_prompt_version = xlsx_review_prompt_version
        self.xlsx_package_limits = xlsx_package_limits or XlsxPackageLimits()
        self.xlsx_visual_mode = xlsx_visual_mode
        self.vision_max_concurrency = vision_max_concurrency

    def extract(
        self,
        path: Path,
        source_filename: str,
        document_type: str,
        content_hash: str,
        content_profile: str = "general_document",
    ) -> NormalizedDocument:
        suffix = Path(source_filename).suffix.lower()
        transcript = document_type == "transcript"
        if suffix in {".txt", ".md", ".markdown"}:
            return extract_text_document(
                path,
                source_filename,
                suffix.removeprefix("."),
                content_hash,
                markdown=suffix in {".md", ".markdown"},
                transcript=transcript,
            )
        if suffix == ".docx":
            return extract_docx(
                path,
                source_filename,
                content_hash,
                transcript=transcript,
            )
        if suffix == ".pdf":
            return extract_pdf(
                path,
                source_filename,
                content_hash,
                document_type=document_type,
                content_profile=content_profile,
                renderer=self.pdf_renderer,
                vision_provider=self.vision_provider,
                review_provider=self.review_provider,
                vision_prompt_version=self.pdf_vision_prompt_version,
                review_prompt_version=self.pdf_review_prompt_version,
                max_concurrency=self.vision_max_concurrency,
            )
        if suffix == ".pptx":
            return extract_pptx(
                path,
                source_filename,
                content_hash,
                content_profile=content_profile,
                renderer=self.pptx_renderer,
                vision_provider=self.vision_provider,
                review_provider=self.review_provider,
                vision_prompt_version=self.vision_prompt_version,
                review_prompt_version=self.review_prompt_version,
                max_concurrency=self.vision_max_concurrency,
            )
        if suffix == ".xlsx":
            return extract_xlsx(
                path,
                source_filename,
                content_hash,
                content_profile=content_profile,
                renderer=self.xlsx_renderer,
                vision_provider=self.vision_provider,
                review_provider=self.review_provider,
                vision_prompt_version=self.xlsx_vision_prompt_version,
                review_prompt_version=self.xlsx_review_prompt_version,
                max_concurrency=self.vision_max_concurrency,
                package_limits=self.xlsx_package_limits,
                visual_mode=self.xlsx_visual_mode,
            )
        raise ExtractionFailure(
            "UNSUPPORTED_FORMAT",
            f"unsupported file format: {suffix or 'no extension'}",
        )
