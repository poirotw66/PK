"""User-visible file intake behavior, with synthetic files only."""

import hashlib
import io
import zipfile

import pymupdf
import pytest
from docx import Document
from openpyxl import Workbook

from squash_scheduler.importers.intake import parse_intake


def spreadsheet(rows, title="Sheet1"):
    workbook = Workbook()
    workbook.active.title = title
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def pdf(text=None):
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((50, 50), text)
    data = document.tobytes()
    document.close()
    return data


def test_excel_rows_preserve_source_coordinates_and_names():
    data = spreadsheet([["姓名", "組別"], ["Synthetic Alice", "男子 A 組"]])
    result = parse_intake("roster.xlsx", data)
    assert result["kind"] == "xlsx"
    assert result["rows"] == [
        {
            "name": "Synthetic Alice",
            "division": "男子 A 組",
            "source_ref": {
                "filename": "roster.xlsx",
                "sha256": hashlib.sha256(data).hexdigest(),
                "sheet": "Sheet1",
                "row": 2,
                "column": 1,
            },
        }
    ]


def test_excel_supports_sheet_division_and_case_insensitive_headers():
    data = spreadsheet([["Player Name"], ["Synthetic Player"]], "男子 B 組")
    result = parse_intake("roster.XLSX", data)
    assert result["rows"][0]["division"] == "男子 B 組"
    assert result["rows"][0]["name"] == "Synthetic Player"


def test_excel_generic_sheet_does_not_invent_division():
    result = parse_intake("roster.xlsx", spreadsheet([["Name"], ["Synthetic Player"]]))
    assert result["rows"][0]["division"] == ""
    assert any("組別" in warning for warning in result["warnings"])


def test_excel_unknown_columns_retain_evidence_without_inventing_players():
    result = parse_intake("rules.xlsx", spreadsheet([["賽制", "說明"], ["循環賽", "待核對"]]))
    assert result["rows"] == []
    assert "循環賽" in result["text"]
    assert any("姓名欄" in warning for warning in result["warnings"])


def test_excel_formula_rejected_without_evaluation():
    with pytest.raises(ValueError, match="公式"):
        parse_intake("roster.xlsx", spreadsheet([["Name", "Division"], ["=1+1", "A"]]))


def test_excel_repeated_header_not_imported_as_player():
    result = parse_intake(
        "roster.xlsx",
        spreadsheet([["Name", "Division"], ["Synthetic A", "A"], ["Name", "Division"]]),
    )
    assert len(result["rows"]) == 1


def test_word_tables_and_numbered_paragraphs_follow_source_order():
    document = Document()
    document.add_paragraph("男子 A 組")
    document.add_paragraph("1. Synthetic A")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "姓名"
    table.cell(0, 1).text = "組別"
    table.cell(1, 0).text = "Synthetic B"
    table.cell(1, 1).text = "女子 B 組"
    document.add_paragraph("比賽規則為三局兩勝，待主辦確認。")
    buffer = io.BytesIO()
    document.save(buffer)
    result = parse_intake("roster.docx", buffer.getvalue())
    assert [(row["name"], row["division"]) for row in result["rows"]] == [
        ("Synthetic A", "男子 A 組"),
        ("Synthetic B", "女子 B 組"),
    ]
    assert result["rows"][0]["source_ref"]["paragraph"] == 2
    assert result["rows"][1]["source_ref"]["table"] == 3
    assert "三局兩勝" in result["text"]


def test_pdf_rules_are_evidence_and_not_players_or_automatically_applied():
    result = parse_intake("rules.pdf", pdf("All matches best of three.\nRest is 60 minutes."))
    assert result["rows"] == []
    assert "Rest is 60 minutes" in result["text"]
    assert any("規則" in warning for warning in result["warnings"])


def test_word_plain_name_paragraphs_under_division_are_candidates():
    document = Document()
    document.add_paragraph("男子 A 組")
    document.add_paragraph("Synthetic Player001")
    document.add_paragraph("虛構甲")
    document.add_paragraph("每場比賽都需要遵守主辦規定。")
    buffer = io.BytesIO()
    document.save(buffer)
    result = parse_intake("roster.docx", buffer.getvalue())
    assert [row["name"] for row in result["rows"]] == ["Synthetic Player001", "虛構甲"]


def test_pdf_only_explicit_numbered_list_under_division_becomes_rows():
    result = parse_intake(
        "roster.pdf", pdf("Men Division\n1. Synthetic A\n2. Synthetic B\nRules: best of three.")
    )
    assert [row["name"] for row in result["rows"]] == ["Synthetic A", "Synthetic B"]
    assert result["rows"][0]["source_ref"]["page"] == 1
    assert result["rows"][0]["source_ref"]["line"] == 2


def test_pdf_numbered_rule_prose_is_not_a_name():
    result = parse_intake(
        "rules.pdf", pdf("Men Division\n1. All matches require at least sixty minutes rest")
    )
    assert result["rows"] == []


def test_pdf_explicit_table_columns():
    result = parse_intake("table.pdf", pdf("Name  Division\nSynthetic A  Men A"))
    assert [(row["name"], row["division"]) for row in result["rows"]] == [("Synthetic A", "Men A")]


def test_scanned_pdf_requires_ocr_and_never_succeeds_as_empty_roster():
    with pytest.raises(ValueError, match="OCR"):
        parse_intake("scan.pdf", pdf())


def test_mixed_pdf_retains_text_and_identifies_pages_needing_ocr():
    document = pymupdf.open()
    document.new_page().insert_text((50, 50), "Synthetic rules")
    document.new_page()
    data = document.tobytes()
    document.close()
    result = parse_intake("mixed.pdf", data)
    assert "Synthetic rules" in result["text"]
    assert any("第 2 頁" in warning and "OCR" in warning for warning in result["warnings"])


@pytest.mark.parametrize("filename,data", [("bad.xlsx", b"broken"), ("bad.docx", b"bad")])
def test_malformed_package_has_actionable_error(filename, data):
    with pytest.raises(ValueError, match="格式"):
        parse_intake(filename, data)


def test_mislabeled_pdf_and_legacy_word_rejected():
    with pytest.raises(ValueError, match="PDF"):
        parse_intake("bad.pdf", b"wrong")
    with pytest.raises(ValueError, match="另存"):
        parse_intake("old.doc", b"not-a-docx")


def test_filename_is_reduced_to_basename():
    result = parse_intake("C:\\private\\roster.xlsx", spreadsheet([["Name"], ["Synthetic A"]]))
    assert result["filename"] == "roster.xlsx"
    assert "private" not in result["text"]


def test_expanded_size_limit_applies_before_parsing():
    with pytest.raises(ValueError, match="大小"):
        parse_intake("roster.xlsx", spreadsheet([["Name"]]), expanded_limit=1)


def test_unsafe_package_path_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape", "synthetic")
    with pytest.raises(ValueError, match="不安全"):
        parse_intake("bad.xlsx", buffer.getvalue())


def test_empty_and_oversize_files_rejected():
    with pytest.raises(ValueError, match="25 MiB"):
        parse_intake("empty.xlsx", b"")
    with pytest.raises(ValueError, match="25 MiB"):
        parse_intake("big.xlsx", b"x" * (25 * 1024 * 1024 + 1))


def test_excel_unknown_headers_offer_explicit_mapping_with_original_coordinates():
    data = spreadsheet(
        [["Competitor", "Class", "Comment"], ["Synthetic A", "Men A", "note"]], "Entries"
    )
    result = parse_intake("custom.xlsx", data)
    assert result["rows"] == []
    table = result["tables"][0]
    assert table["sheet"] == "Entries"
    assert table["name_column"] is None
    assert table["header_row"] == 1
    assert table["rows"][1]["values"] == ["Synthetic A", "Men A", "note"]
    assert table["rows"][1]["source_ref"]["row"] == 2
    assert table["rows"][1]["source_ref"]["sha256"] == hashlib.sha256(data).hexdigest()


def test_excel_mapping_defaults_follow_detected_header_after_notes():
    result = parse_intake(
        "custom.xlsx",
        spreadsheet([["Instructions"], [], ["Division", "Name"], ["A", "Synthetic A"]]),
    )
    table = result["tables"][0]
    assert table["header_row"] == 3
    assert table["name_column"] == 2
    assert table["division_column"] == 1
    assert result["rows"][0]["source_ref"]["row"] == 4
