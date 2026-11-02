"""Anonymous evidence fixtures; no historical private data in source control."""

from __future__ import annotations

import copy
import hashlib

import openpyxl
import pymupdf
import pytest

from squash_scheduler.importers.historical_schedule import (
    audit_pdf_locations,
    compare_schedules,
    extract_excel_schedule,
    extract_pdf_schedule,
    validate_evidence,
)


def _pdf_fixture(path, *, include_date=True):
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    if include_date:
        page.insert_text((40, 35), "2030年6月8日", fontname="china-s", fontsize=12)
    xs = [30, 90, 210, 330, 450, 570]
    ys = [60, 95, 130, 165, 200, 235]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    values = [
        ["場次", "預計時間", "組別", "選手", ""],
        ["8", "9點00分", "男子F", "Player A", "Player B"],
        ["9", "9點15分", "男子F決賽", "Player C", ""],
        ["10", "9點30分", "男子F冠亞", "", ""],
        ["", "", "表演賽", "Player A", "Player C"],
    ]
    for row_index, row in enumerate(values):
        for col_index, value in enumerate(row):
            page.insert_text(
                (xs[col_index] + 4, ys[row_index] + 23), value, fontname="china-s", fontsize=11
            )
    document.save(path)
    document.close()


def test_pdf_geometry_missing_sides_and_exhibition_are_preserved(tmp_path):
    path = tmp_path / "anonymous.pdf"
    _pdf_fixture(path)
    evidence = extract_pdf_schedule(path)
    assert [r["display_code"] for r in evidence["rows"]] == ["8", "9", "10"]
    assert evidence["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    first, second, third = evidence["rows"]
    assert first["date"] == "2030-06-08"
    assert first["local_time"] == "09:00"
    assert first["round_label"] is None
    assert second["round_label"] == "決賽"
    assert second["side_b_display"] is None
    assert third["round_label"] == "冠亞"
    assert third["side_a_display"] is None
    assert all(
        r["court"] is None
        and r["side_a_participant_id"] is None
        and r["side_b_participant_id"] is None
        for r in evidence["rows"]
    )
    exhibition = evidence["unnumbered_exhibitions"][0]
    assert exhibition["display_code"] is None
    assert exhibition["local_time"] is None
    assert "local_time" in exhibition["unknown_fields"]
    with pymupdf.open(path) as document:
        for row in evidence["rows"]:
            source = row["source"]
            assert source["page"] == 1
            assert document[0].get_textbox(pymupdf.Rect(source["bbox"])) == source["text"]
            assert source["cells"][0]["text"] == row["display_code"]
    checks = validate_evidence(evidence, {"8", "9", "10"})
    assert checks["structural_checks_passed"]
    assert checks["known_display_sides"] == {"0": 1, "1": 1, "2": 1}
    audit = audit_pdf_locations(evidence, path)
    assert audit["source_location_checks_passed"]
    assert audit["checked_rows_including_exhibitions"] == 4


def test_missing_pdf_date_is_not_filled(tmp_path):
    path = tmp_path / "anonymous.pdf"
    _pdf_fixture(path, include_date=False)
    evidence = extract_pdf_schedule(path)
    assert all(row["date"] is None and row["date_source"] is None for row in evidence["rows"])
    assert not validate_evidence(evidence)["structural_checks_passed"]


def test_excel_preserves_extra_labels_and_formula_text(tmp_path):
    path = tmp_path / "anonymous.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Day one"
    sheet["B1"] = "2030年6月8日"
    for col, text in ((2, "場次"), (3, "預計時間"), (5, "組別"), (6, "選手")):
        sheet.cell(2, col, text)
    for row, values in enumerate(
        [
            [8, "9點", "00分", "男子F", "Player A", "Player B"],
            ["8A", "9點", "15分", "男子F", "Player C", "=1+1"],
            [None, None, None, "表演賽", "Player A", "Player C"],
        ],
        3,
    ):
        for col, value in enumerate(values, 2):
            sheet.cell(row, col, value)
    workbook.save(path)
    evidence = extract_excel_schedule(path)
    assert len(evidence["rows"]) == 1
    assert evidence["rows"][0]["source"]["range"] == "B3:G3"
    extra = evidence["additional_display_rows"][0]
    assert extra["display_code"] == "8A"
    assert extra["side_b_display"] == "=1+1"
    assert extra["source"]["cells"][-1]["data_type"] == "f"
    assert evidence["authority"] == "working_not_published"
    assert not evidence["issues"]


def test_diff_has_both_source_locations_and_counts_overlap_once(tmp_path):
    path = tmp_path / "anonymous.pdf"
    _pdf_fixture(path)
    baseline = extract_pdf_schedule(path)
    working = copy.deepcopy(baseline)
    working["rows"][0]["side_a_display"] = "Player Z"
    working["rows"][0]["local_time"] = "10:00"
    working["rows"][1]["side_a_display"] = "Player    C"
    diff = compare_schedules(baseline, working)
    assert diff["difference_count"] == 1
    assert diff["personnel_and_timing_codes"] == ["8"]
    assert diff["differences"][0]["published"]["source"]["bbox"]
    assert diff["differences"][0]["working"]["source"]["bbox"]
    working["rows"].append(working["rows"][0])
    with pytest.raises(ValueError, match="duplicate"):
        compare_schedules(baseline, working)


def test_validation_detects_missing_duplicate_and_tampered_location(tmp_path):
    path = tmp_path / "anonymous.pdf"
    _pdf_fixture(path)
    evidence = extract_pdf_schedule(path)
    evidence["rows"].append(copy.deepcopy(evidence["rows"][0]))
    evidence["rows"][0]["source"]["bbox"] = [0, 0, 0, 0]
    result = validate_evidence(evidence, {"8", "9", "10", "11"})
    assert result["duplicate_codes"] == ["8"]
    assert result["missing_codes"] == ["11"]
    assert result["errors"][0]["code"] == "SOURCE_LOCATION_INVALID"
    assert not result["structural_checks_passed"]
    evidence["rows"][0]["source"]["page"] = 99
    assert audit_pdf_locations(evidence, path)["errors"][0]["code"] == "SOURCE_PAGE_INVALID"
