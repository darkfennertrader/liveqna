from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .common import DocumentBuilder
from .errors import ExtractionFailure
from .models import NormalizedDocument, SourceLocator


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_xlsx(
    path: Path, source_filename: str, content_hash: str
) -> NormalizedDocument:
    try:
        workbook = load_workbook(
            path, read_only=True, data_only=False, keep_links=False
        )
    except Exception as exc:
        raise ExtractionFailure(
            "CORRUPT_DOCUMENT", "XLSX file could not be opened"
        ) from exc

    builder = DocumentBuilder(source_filename, "xlsx", content_hash)
    empty_sheets: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            rows: list[tuple[int, list[str], int, int]] = []
            for row_number, cells in enumerate(
                worksheet.iter_rows(values_only=True), start=1
            ):
                values = [_cell_text(value) for value in cells]
                non_empty = [index for index, value in enumerate(values) if value]
                if not non_empty:
                    continue
                first, last = min(non_empty), max(non_empty)
                rows.append((row_number, values[first : last + 1], first + 1, last + 1))
            if not rows:
                empty_sheets.append(worksheet.title)
                continue
            builder.heading(
                worksheet.title,
                SourceLocator(worksheet=worksheet.title),
                level=1,
            )
            for row_index, (row_number, values, first_col, last_col) in enumerate(rows):
                cell_range = (
                    f"{get_column_letter(first_col)}{row_number}:"
                    f"{get_column_letter(last_col)}{row_number}"
                )
                builder.add(
                    "table_header" if row_index == 0 else "table_row",
                    "\t".join(values),
                    SourceLocator(
                        worksheet=worksheet.title, cell_range=cell_range
                    ),
                )
    finally:
        workbook.close()
    if empty_sheets:
        builder.warnings.append(
            "worksheets without values: " + ", ".join(empty_sheets)
        )
    return builder.build()
