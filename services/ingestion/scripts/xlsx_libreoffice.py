#!/usr/bin/env python3
"""Render selected Calc worksheets and save a recalculated workbook copy.

This helper intentionally runs under the system Python installed with
python3-uno. The ingestion service launches a private LibreOffice process with
an isolated profile and no link updates or macro execution.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import uno
from com.sun.star.beans import PropertyValue


def property_value(name: str, value) -> PropertyValue:
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def office_context(port: int, timeout: float):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            return resolver.resolve(
                f"uno:socket,host=127.0.0.1,port={port};urp;"
                "StarOffice.ComponentContext"
            )
        except Exception as exc:  # UNO raises generated runtime exception types
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"LibreOffice listener did not start: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--soffice", default="soffice")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--sheet-index", type=int, action="append", default=[])
    arguments = parser.parse_args()

    source = Path(arguments.source).resolve()
    output = Path(arguments.output_dir).resolve()
    profile = Path(arguments.profile_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)
    port = free_port()
    command = [
        arguments.soffice,
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--norestore",
        f"--accept=socket,host=127.0.0.1,port={port};urp;",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    document = None
    try:
        context = office_context(port, min(arguments.timeout, 30.0))
        service_manager = context.ServiceManager
        desktop = service_manager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context
        )
        document = desktop.loadComponentFromURL(
            source.as_uri(),
            "_blank",
            0,
            (
                property_value("Hidden", True),
                property_value("ReadOnly", False),
                property_value("UpdateDocMode", 0),
                property_value("MacroExecutionMode", 0),
            ),
        )
        if document is None or not document.supportsService(
            "com.sun.star.sheet.SpreadsheetDocument"
        ):
            raise RuntimeError("LibreOffice did not open an XLSX spreadsheet")

        document.calculateAll()
        recalculated = output / "recalculated.xlsx"
        document.storeToURL(
            recalculated.as_uri(),
            (
                property_value("FilterName", "Calc MS Excel 2007 XML"),
                property_value("Overwrite", True),
            ),
        )

        sheets = document.getSheets()
        names = list(sheets.getElementNames())
        selected = sorted(set(arguments.sheet_index)) or list(range(1, len(names) + 1))
        original_visibility = [bool(sheets.getByIndex(i).IsVisible) for i in range(len(names))]
        rendered = []
        for one_based_index in selected:
            if one_based_index < 1 or one_based_index > len(names):
                raise RuntimeError(f"worksheet index is out of range: {one_based_index}")
            target_index = one_based_index - 1
            for index in range(len(names)):
                sheets.getByIndex(index).IsVisible = index == target_index
            target = sheets.getByIndex(target_index)
            pdf = output / f"worksheet-{one_based_index:04d}.pdf"
            document.storeToURL(
                pdf.as_uri(),
                (
                    property_value("FilterName", "calc_pdf_Export"),
                    property_value("Overwrite", True),
                ),
            )
            rendered.append(
                {
                    "worksheet_index": one_based_index,
                    "title": names[target_index],
                    "pdf": pdf.name,
                }
            )
            target.IsVisible = True

        for index, visible in enumerate(original_visibility):
            sheets.getByIndex(index).IsVisible = visible

        manifest = {
            "recalculated_workbook": recalculated.name,
            "worksheets": rendered,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if document is not None:
            try:
                document.close(True)
            except Exception:
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
