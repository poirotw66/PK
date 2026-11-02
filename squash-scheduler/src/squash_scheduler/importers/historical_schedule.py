"""Read-only historical schedule evidence; never creates accepted event records.

The PDF is the published baseline. Excel daily sheets remain separate working
evidence. Displayed names are text observations, not participant identities.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl
import pymupdf

PARSER_VERSION = "historical-daily-tables/1.0"
DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
TIME_RE = re.compile(r"(\d{1,2})\s*點\s*(\d{1,2})\s*分")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date(text: str) -> str | None:
    match = DATE_RE.search(text)
    return date(*map(int, match.groups())).isoformat() if match else None


def _time(text: str) -> str | None:
    match = TIME_RE.fullmatch(text.strip())
    if not match:
        return None
    hour, minute = map(int, match.groups())
    return f"{hour:02}:{minute:02}" if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def _label(text: str) -> tuple[str | None, str | None]:
    for suffix in ("敗部", "決賽", "冠亞", "季殿"):
        if text.endswith(suffix):
            return text[: -len(suffix)], suffix
    return text or None, None


def _record(
    cells: Sequence[str | None],
    evidence: dict[str, Any],
    date_value: str | None,
    date_source: dict[str, Any] | None,
) -> dict[str, Any]:
    padded = [v or "" for v in cells] + [""] * (5 - len(cells))
    code, time_text, label, side_a, side_b = padded[:5]
    division, round_label = _label(label)
    unknown = ["court", "side_a_participant_id", "side_b_participant_id"]
    local_time = _time(time_text)
    for field, value in (
        ("date", date_value),
        ("local_time", local_time),
        ("round_label", round_label),
        ("side_a_display", side_a),
        ("side_b_display", side_b),
    ):
        if not value:
            unknown.append(field)
    return {
        "status": "draft_evidence",
        "display_code": code or None,
        "date": date_value,
        "date_source": date_source,
        "local_time": local_time,
        "time_text": time_text or None,
        "division_label": division,
        "round_label": round_label,
        "division_round_text": label or None,
        "side_a_display": side_a or None,
        "side_b_display": side_b or None,
        "side_a_participant_id": None,
        "side_b_participant_id": None,
        "court": None,
        "unknown_fields": unknown,
        "source": evidence,
    }


def extract_pdf_schedule(path: str | Path) -> dict[str, Any]:
    """Extract ruled five-column daily tables, preserving page/cell geometry.

    Detects the header rather than assuming page numbers. Missing dates, times,
    names and rounds stay unknown; no cross-row or cross-page fill is performed.
    """
    path = Path(path)
    digest = _sha256(path)
    rows: list[dict[str, Any]] = []
    exhibitions: list[dict[str, Any]] = []
    pages = []
    issues = []
    with pymupdf.open(path) as document:
        for index, page in enumerate(document):
            date_sources: list[dict[str, Any]] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"])
                    if value := _date(text):
                        date_sources.append(
                            {
                                "date": value,
                                "page": index + 1,
                                "bbox": list(line["bbox"]),
                                "text": text,
                                "sha256": digest,
                            }
                        )
            unique_dates = {item["date"] for item in date_sources}
            date_source = date_sources[0] if len(unique_dates) == 1 else None
            date_value = date_source["date"] if date_source else None
            page_rows = 0
            for table in page.find_tables().tables:
                extracted = table.extract()
                if table.col_count != 5 or not extracted:
                    continue
                header = extracted[0]
                if header[:3] != ["場次", "預計時間", "組別"]:
                    continue
                for row_index, cells in enumerate(extracted[1:], 1):
                    code = (cells[0] or "").strip()
                    if not code.isdigit() and cells[2] != "表演賽":
                        issues.append(
                            {
                                "code": "UNPARSED_DAILY_ROW",
                                "page": index + 1,
                                "table_row": row_index + 1,
                                "cells": cells,
                            }
                        )
                        continue
                    boxes = table.rows[row_index].cells
                    actual_boxes = [b for b in boxes if b]
                    bbox = [
                        min(b[0] for b in actual_boxes),
                        min(b[1] for b in actual_boxes),
                        max(b[2] for b in actual_boxes),
                        max(b[3] for b in actual_boxes),
                    ]
                    evidence = {
                        "kind": "published_pdf",
                        "sha256": digest,
                        "file_name": path.name,
                        "page": index + 1,
                        "table_row": row_index + 1,
                        "bbox": bbox,
                        "text": page.get_textbox(pymupdf.Rect(bbox)),
                        "cells": [
                            {"bbox": list(box) if box else None, "text": text}
                            for box, text in zip(boxes, cells, strict=True)
                        ],
                    }
                    record = _record(cells, evidence, date_value, date_source)
                    (rows if code.isdigit() else exhibitions).append(record)
                    page_rows += bool(code.isdigit())
                pages.append(
                    {
                        "page": index + 1,
                        "numbered_rows": page_rows,
                        "date": date_value,
                        "table_bbox": list(table.bbox),
                    }
                )
    return {
        "schema_version": "1.0.0",
        "parser_version": PARSER_VERSION,
        "status": "draft_evidence",
        "source_sha256": digest,
        "source_file_name": path.name,
        "authority": "published_baseline",
        "rows": rows,
        "unnumbered_exhibitions": exhibitions,
        "additional_display_rows": [],
        "pages": pages,
        "issues": issues,
    }


def extract_excel_schedule(path: str | Path) -> dict[str, Any]:
    """Read only explicitly headed daily sheets; retain each original cell value."""
    path = Path(path)
    digest = _sha256(path)
    workbook = openpyxl.load_workbook(path, data_only=False)
    rows: list[dict[str, Any]] = []
    exhibitions: list[dict[str, Any]] = []
    additional: list[dict[str, Any]] = []
    sheets = []
    issues = []
    for sheet in workbook:
        if [sheet.cell(2, col).value for col in (2, 3, 5, 6)] != [
            "場次",
            "預計時間",
            "組別",
            "選手",
        ]:
            continue
        sheets.append(sheet.title)
        date_text = str(sheet["B1"].value or "")
        date_value = _date(date_text)
        date_source = {"sha256": digest, "sheet": sheet.title, "cell": "B1", "text": date_text}
        for row_index in range(3, sheet.max_row + 1):
            source_cells = [sheet.cell(row_index, col) for col in range(2, 8)]
            values = [cell.value for cell in source_cells]
            code = values[0]
            additional_code = isinstance(code, str) and bool(re.fullmatch(r"\d+[A-Z]", code))
            if not isinstance(code, int) and not additional_code and values[3] != "表演賽":
                if any(value is not None for value in values):
                    issues.append(
                        {
                            "code": "UNPARSED_DAILY_ROW",
                            "sheet": sheet.title,
                            "row": row_index,
                            "values": values,
                        }
                    )
                continue
            evidence = {
                "kind": "working_excel",
                "sha256": digest,
                "file_name": path.name,
                "sheet": sheet.title,
                "row": row_index,
                "range": f"B{row_index}:G{row_index}",
                "cells": [
                    {"cell": cell.coordinate, "value": cell.value, "data_type": cell.data_type}
                    for cell in source_cells
                ],
            }
            time_text = " ".join(str(v) for v in values[1:3] if v is not None)
            cells = [
                str(code) if code is not None else "",
                time_text,
                *[str(v) if v is not None else "" for v in values[3:]],
            ]
            record = _record(cells, evidence, date_value, date_source)
            target = (
                rows if isinstance(code, int) else additional if additional_code else exhibitions
            )
            target.append(record)
    workbook.close()
    return {
        "schema_version": "1.0.0",
        "parser_version": PARSER_VERSION,
        "status": "draft_evidence",
        "source_sha256": digest,
        "source_file_name": path.name,
        "authority": "working_not_published",
        "rows": rows,
        "unnumbered_exhibitions": exhibitions,
        "additional_display_rows": additional,
        "sheets": sheets,
        "issues": issues,
    }


def validate_evidence(
    bundle: dict[str, Any], expected_codes: set[str] | None = None
) -> dict[str, Any]:
    """Structural evidence checks, independent of any tournament approval gate."""
    rows = bundle["rows"]
    counts = Counter(row["display_code"] for row in rows)
    errors = []
    for row in rows:
        source = row["source"]
        code = row["display_code"]
        if source["sha256"] != bundle["source_sha256"]:
            errors.append({"code": "SOURCE_HASH_MISMATCH", "display_code": code})
        if source["kind"] == "published_pdf":
            bbox = source.get("bbox", [])
            if (
                not source.get("page")
                or len(bbox) != 4
                or bbox[0] >= bbox[2]
                or bbox[1] >= bbox[3]
                or not source.get("text")
            ):
                errors.append({"code": "SOURCE_LOCATION_INVALID", "display_code": code})
            if source["cells"][0]["text"] != code:
                errors.append({"code": "DISPLAY_CODE_SOURCE_MISMATCH", "display_code": code})
        elif not source.get("sheet") or not source.get("range"):
            errors.append({"code": "SOURCE_LOCATION_INVALID", "display_code": code})
        if row["status"] != "draft_evidence" or row["court"] is not None:
            errors.append({"code": "UNAUTHORIZED_INFERENCE", "display_code": code})
        if not row["local_time"] or not row["date"] or not row["division_label"]:
            errors.append({"code": "SCHEDULE_FIELD_UNKNOWN", "display_code": code})
    duplicate_codes = sorted(code for code, count in counts.items() if count > 1)
    missing = sorted((expected_codes or set()) - set(counts), key=int)
    unexpected = sorted(set(counts) - expected_codes, key=int) if expected_codes else []
    return {
        "status": "draft_evidence",
        "numbered_rows": len(rows),
        "unique_codes": len(counts),
        "duplicate_codes": duplicate_codes,
        "missing_codes": missing,
        "unexpected_codes": unexpected,
        "known_display_sides": {
            str(n): sum(bool(r["side_a_display"]) + bool(r["side_b_display"]) == n for r in rows)
            for n in range(3)
        },
        "unnumbered_exhibitions": len(bundle["unnumbered_exhibitions"]),
        "additional_display_rows": len(bundle.get("additional_display_rows", [])),
        "errors": errors,
        "parser_issues": bundle["issues"],
        "structural_checks_passed": not (
            errors or duplicate_codes or missing or unexpected or bundle["issues"]
        ),
        "approval": None,
    }


def _display_comparison(value: str | None) -> str | None:
    # Layout spaces differ between PDF words and Excel cells. The original
    # strings remain in both records; this normalization never resolves identity.
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value)) if value else None


def audit_pdf_locations(bundle: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """Reopen the original and check every recorded row and cell against it."""
    digest = _sha256(Path(path))
    errors: list[dict[str, Any]] = []
    records = [
        *bundle["rows"],
        *bundle["unnumbered_exhibitions"],
        *bundle.get("additional_display_rows", []),
    ]
    if digest != bundle["source_sha256"]:
        errors.append({"code": "SOURCE_HASH_MISMATCH"})
    with pymupdf.open(path) as document:
        for row in records:
            source = row["source"]
            location = {"display_code": row["display_code"], "page": source["page"]}
            if not 1 <= source["page"] <= len(document):
                errors.append({"code": "SOURCE_PAGE_INVALID", **location})
                continue
            page = document[source["page"] - 1]
            bbox = pymupdf.Rect(source["bbox"])
            if not page.rect.contains(bbox) or page.get_textbox(bbox) != source["text"]:
                errors.append({"code": "SOURCE_ROW_TEXT_MISMATCH", **location})
            for column, cell in enumerate(source["cells"]):
                if cell["bbox"] is None:
                    continue
                actual = page.get_textbox(pymupdf.Rect(cell["bbox"]))
                if (_display_comparison(actual) or None) != (
                    _display_comparison(cell["text"]) or None
                ):
                    errors.append(
                        {"code": "SOURCE_CELL_TEXT_MISMATCH", "column": column, **location}
                    )
            if date_source := row["date_source"]:
                actual_date = page.get_textbox(pymupdf.Rect(date_source["bbox"]))
                if _date(actual_date) != row["date"]:
                    errors.append({"code": "SOURCE_DATE_MISMATCH", **location})
    return {
        "status": "draft_evidence",
        "source_sha256": digest,
        "checked_rows_including_exhibitions": len(records),
        "errors": errors,
        "source_location_checks_passed": not errors,
        "approval": None,
    }


def compare_schedules(published: dict[str, Any], working: dict[str, Any]) -> dict[str, Any]:
    """Compare same display numbers with complete evidence on both sides."""
    for bundle in (published, working):
        codes = [row["display_code"] for row in bundle["rows"]]
        if len(codes) != len(set(codes)):
            raise ValueError("Cannot compare evidence with duplicate display codes")
    baseline = {row["display_code"]: row for row in published["rows"]}
    candidate = {row["display_code"]: row for row in working["rows"]}
    differences = []
    for code in sorted(baseline.keys() | candidate.keys(), key=int):
        pdf, excel = baseline.get(code), candidate.get(code)
        fields = []
        if pdf is None or excel is None:
            fields = ["row_presence"]
        else:
            for field in (
                "date",
                "local_time",
                "division_round_text",
                "side_a_display",
                "side_b_display",
            ):
                if _display_comparison(pdf[field]) != _display_comparison(excel[field]):
                    fields.append(field)
        if fields:
            differences.append(
                {"display_code": code, "fields": fields, "published": pdf, "working": excel}
            )
    personnel = [
        r["display_code"]
        for r in differences
        if {"side_a_display", "side_b_display"} & set(r["fields"])
    ]
    timing = [r["display_code"] for r in differences if {"date", "local_time"} & set(r["fields"])]
    return {
        "status": "draft_evidence",
        "authority": "pdf_is_published_baseline",
        "published_sha256": published["source_sha256"],
        "working_sha256": working["source_sha256"],
        "comparison": "same display_code; NFC and whitespace-insensitive text only",
        "scope": "numbered rows; extra working display labels reported separately",
        "identity_resolution": None,
        "difference_count": len(differences),
        "personnel_display_difference_codes": personnel,
        "timing_difference_codes": timing,
        "personnel_and_timing_codes": sorted(set(personnel) & set(timing), key=int),
        "differences": differences,
        "additional_working_display_rows": working.get("additional_display_rows", []),
        "additional_published_display_rows": published.get("additional_display_rows", []),
    }
