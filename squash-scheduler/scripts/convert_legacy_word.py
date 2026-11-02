"""One-off controlled Word conversion for historical review, using installed pywin32.

The application default remains LibreOffice. This helper requires Microsoft Word
and pywin32 in the selected Python environment. It never overwrites the source.
https://learn.microsoft.com/en-us/office/vba/api/word.application.automationsecurity
https://learn.microsoft.com/en-us/office/vba/api/word.documents.open
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def convert(source: Path, destination: Path) -> dict:
    import pythoncom
    import win32com.client

    source, destination = source.resolve(strict=True), destination.resolve()
    if source.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Source exceeds the controlled conversion size limit")
    destination.mkdir(parents=True, exist_ok=False)
    output = destination / "roster.converted.docx"
    report_path = destination / "conversion-report.json"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report = {
        "status": "running",
        "worker_pid": os.getpid(),
        "source_path": str(source),
        "source_sha256": source_hash,
        "converter": "Microsoft Word controlled historical-review copy",
        "macros": "force_disabled",
        "update_links_at_open": False,
        "read_only": True,
        "add_to_recent_files": False,
        "started_at": datetime.now(UTC).isoformat(),
    }

    def save() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    word, document = None, None
    old_security, old_links = None, None
    pythoncom.CoInitialize()
    save()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        old_security = word.AutomationSecurity
        old_links = word.Options.UpdateLinksAtOpen
        word.AutomationSecurity = 3
        word.Options.UpdateLinksAtOpen = False
        report.update(converter_version=word.Version)
        save()
        document = word.Documents.Open(
            str(source),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
            OpenAndRepair=False,
            NoEncodingDialog=True,
        )
        document.SaveAs2(str(output), FileFormat=12)
        document.Close(SaveChanges=0)
        document = None
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
            raise ValueError("Original source checksum changed")
        report.update(
            status="converted_for_historical_review",
            original_unchanged=True,
            output_path=str(output),
            converted_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        )
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            if document is not None:
                document.Close(SaveChanges=0)
            if word is not None:
                if old_security is not None:
                    word.AutomationSecurity = old_security
                if old_links is not None:
                    word.Options.UpdateLinksAtOpen = old_links
                word.Quit(SaveChanges=0)
        finally:
            document, word = None, None
            pythoncom.CoUninitialize()
            report["completed_at"] = datetime.now(UTC).isoformat()
            save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(convert(args.source, args.output), ensure_ascii=True, indent=2))
