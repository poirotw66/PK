from pathlib import Path
from runpy import run_path
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from openpyxl import Workbook, load_workbook

from squash_scheduler.importers.historical_graphs import (
    extract_source_graphs,
    roster_from_import_report,
)


def test_workbook_source_formula_strings_stay_literal(tmp_path: Path):
    helper = run_path(str(Path(__file__).parents[1] / "examples/build_full_event_demo.py"))
    workbook = Workbook()
    formula = '=HYPERLINK("https://example.invalid", "Anonymous")'
    workbook.active.append([formula, "+1", "-1", "@name"])
    helper["preserve_excel_strings"](workbook)
    target = tmp_path / "literal.xlsx"
    workbook.save(target)
    restored = load_workbook(target)
    assert restored.active["A1"].value == formula
    assert all(cell.data_type == "s" for cell in restored.active[1])


def test_exact_name_identity_preserves_internal_spaces():
    candidates = [
        {"division_id": "d1", "official_name": "Division A", "ignored_reason": "division_heading"},
        {"division_id": "d2", "official_name": "Division B", "ignored_reason": "division_heading"},
    ]
    for index, (name, division) in enumerate(
        [("Alex Example", "d1"), ("Alex Example", "d2"), ("Alex  Example", "d2")]
    ):
        candidates.append(
            {
                "id": str(index),
                "official_name": name,
                "division_id": division,
                "ignored_reason": None,
                "source_ref": {"line": index},
            }
        )
    roster = roster_from_import_report({"candidates": candidates})
    assert roster[0]["participant_id"] == roster[1]["participant_id"]
    assert roster[1]["participant_id"] != roster[2]["participant_id"]
    assert len({r["registration_id"] for r in roster}) == 3
    assert roster[2]["name"] == "Alex  Example"
    assert roster_from_import_report({"candidates": candidates}) == roster


def test_export_requires_identifiable_source_heading(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 50), "Anonymous source page")
        document.save(pdf)
    with pytest.raises(ValueError, match="division heading"):
        extract_source_graphs(pdf, tmp_path / "out", page_count=1)


def test_export_preserves_page_paths_and_source_metadata(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "\u7537\u5b50B\u7d44", fontname="china-t")
        page.draw_line((40, 80), (140, 80))
        document.save(pdf)
    metadata = extract_source_graphs(pdf, tmp_path / "out", page_count=1)
    item = metadata["bracket-source-001.svg"]
    root = ET.parse(tmp_path / "out/bracket-source-001.svg").getroot()
    assert root.get("data-division") == "\u7537\u5b50B\u7d44"
    assert root.get("data-source-page") == "1"
    assert item["source_page"] == 1
    assert item["presentation"] == "original_source_page"
    assert len(item["source_sha256"]) == 64
    assert len(item["svg_sha256"]) == 64
    assert any(node.tag.endswith("path") for node in root.iter())
