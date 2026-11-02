"""Document safety, shared output consistency, and atomic release failure checks."""

from __future__ import annotations

import base64
import io
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pymupdf
import pytest
import zxingcpp
from openpyxl import load_workbook
from PIL import Image
from test_formats_draws import event

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Assignment, new_id
from squash_scheduler.exporters import documents


def document_event(count=3):
    snapshot, division = event(count)
    division.name = "S"
    for index, person in enumerate(snapshot.participants):
        person.display_name = f"P{index}"
    snapshot.config.update(
        event={
            "name": "Synthetic Tournament",
            "venue_name": "Test Venue",
            "timezone": "Asia/Taipei",
        },
        publication={"qr_mode": "disabled", "sponsor_mode": "none"},
    )
    generate_matches(snapshot)
    court_id = new_id()
    snapshot.config["courts"] = [{"id": court_id, "label": "Court A", "sort_order": 1}]
    start = datetime(2027, 1, 2, 0, 0, tzinfo=UTC)
    for index, match in enumerate(snapshot.matches):
        match.display_code = f"M{index + 1:03}"
        at = start + timedelta(minutes=30 * index)
        snapshot.assignments.append(
            Assignment(
                match_id=match.id,
                court_id=court_id,
                start_at=at,
                play_end_at=at + timedelta(minutes=25),
                court_release_at=at + timedelta(minutes=30),
            )
        )
    return snapshot


def synthetic_pdf(model, path):
    """Purposefully minimal test PDF; this never constitutes a visually reviewed release."""
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((40, 25), "Synthetic test artifact", fontsize=9)
    margin = 14 * 72 / 25.4
    cell_width = (page.rect.width - 2 * margin) / 5
    for index, row in enumerate(model["matches"]):
        cells = [
            f"[{row['code']}]",
            f"{row['date']} {row['time']}",
            row["court"],
            f"{row['division']} · {row['stage']} · {row['kind']}",
            f"{row['side_a']} 對 {row['side_b']}",
        ]
        for column, text in enumerate(cells):
            page.insert_text(
                (margin + column * cell_width + 5, 55 + index * 25),
                text,
                fontsize=9,
                fontname="china-t",
            )
    for summary in model.get("rule_summaries", []):
        page = doc.new_page(width=842, height=595)
        for index, line in enumerate(summary["lines"]):
            page.insert_text((40, 50 + index * 24), line, fontsize=9, fontname="china-t")
    doc.save(path)
    doc.close()


def test_html_and_svg_escape_untrusted_names_sponsors_and_event(tmp_path):
    snapshot = document_event()
    attack = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    snapshot.participants[0].display_name = attack
    snapshot.divisions[0].name = attack
    snapshot.config["event"]["name"] = attack
    snapshot.config["publication"].update(
        sponsor_mode="provided", sponsors=[{"display_name": attack, "text": attack}]
    )
    html = documents.render_html(documents.view_model(snapshot), tmp_path)
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    for path in tmp_path.glob("*.svg"):
        root = ElementTree.fromstring(path.read_text("utf-8"))
        assert not root.findall(".//{http://www.w3.org/2000/svg}script")


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_external_formula_like_text_is_saved_as_text_in_every_sheet(tmp_path, prefix):
    snapshot = document_event()
    value = prefix + 'HYPERLINK("https://example.test","test")'
    snapshot.participants[0].display_name = value
    snapshot.participants[0].official_name = value
    snapshot.config["event"]["name"] = value
    model = documents.view_model(snapshot)
    path = tmp_path / "schedule.xlsx"
    documents.export_excel(snapshot, model, path)
    wb = load_workbook(path, data_only=False)
    found = [c for ws in wb for row in ws for c in row if c.value == value]
    assert found and all(cell.data_type == "s" for cell in found)
    assert not any(c.data_type == "f" for ws in wb for row in ws for c in row)
    assert {
        "Metadata",
        "Participants",
        "Registrations",
        "Divisions",
        "Matches",
        "Schedule",
        "Issues",
        "Changes",
    } <= set(wb.sheetnames)


def test_shared_model_orders_schedule_local_time_and_excludes_cancelled_matches(tmp_path):
    snapshot = document_event()
    snapshot.matches[0].status = "excluded"
    snapshot.matches[1].status = "cancelled"
    model = documents.view_model(snapshot)
    assert len(model["matches"]) == 1
    assert model["matches"][0]["time"] == "09:00"
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    wb = load_workbook(tmp_path / "schedule.xlsx", data_only=False)
    keys = [c.value for c in wb["Schedule"][1]]
    actual = [
        dict(zip(keys, values, strict=True))
        for values in wb["Schedule"].iter_rows(min_row=2, values_only=True)
    ]
    assert actual == [{k: row[k] for k in keys} for row in model["matches"]]
    html = documents.render_html(model, tmp_path)
    assert snapshot.matches[2].display_code in html
    assert snapshot.matches[0].display_code not in html


def test_embedded_qr_roundtrips_exact_https_url_with_query_and_quiet_zone(tmp_path):
    snapshot = document_event()
    url = "https://example.test/notice?division=A&lang=zh-TW"
    snapshot.config["publication"].update(
        qr_mode="enabled", qr_url=url, qr_label="Synthetic notice"
    )
    html = documents.render_html(documents.view_model(snapshot), tmp_path)
    encoded = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html).group(1)
    picture = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert [r.text for r in zxingcpp.read_barcodes(picture)] == [url]
    # At least the four-module quiet zone appears as an all-white image border.
    assert picture.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
    assert "division=A&amp;lang=zh-TW" in html


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.test", "file:///secret"])
def test_qr_rejects_non_https_urls(tmp_path, url):
    snapshot = document_event()
    snapshot.config["publication"].update(qr_mode="enabled", qr_url=url, qr_label="test")
    with pytest.raises(ValueError, match="HTTPS"):
        documents.render_html(documents.view_model(snapshot), tmp_path)


def test_qa_rejects_workbook_changes_and_missing_pdf_match_text(tmp_path):
    snapshot = document_event()
    model = documents.view_model(snapshot)
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    synthetic_pdf(model, tmp_path / "booklet.pdf")
    assert documents.document_qa(model, tmp_path)["passed"]
    wb = load_workbook(tmp_path / "schedule.xlsx")
    wb["Schedule"]["D2"] = "Wrong time"
    wb.save(tmp_path / "schedule.xlsx")
    with pytest.raises(ValueError, match="spreadsheet rows differ"):
        documents.document_qa(model, tmp_path)
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    missing = {**model, "matches": model["matches"][:-1]}
    (tmp_path / "booklet.pdf").unlink()
    synthetic_pdf(missing, tmp_path / "booklet.pdf")
    with pytest.raises(ValueError, match="PDF omits"):
        documents.document_qa(model, tmp_path)


def test_qa_rejects_pdf_with_swapped_schedule_times_even_if_all_tokens_exist(tmp_path):
    snapshot = document_event()
    model = documents.view_model(snapshot)
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    swapped = {**model, "matches": [dict(row) for row in model["matches"]]}
    swapped["matches"][0]["time"], swapped["matches"][1]["time"] = (
        swapped["matches"][1]["time"],
        swapped["matches"][0]["time"],
    )
    synthetic_pdf(swapped, tmp_path / "booklet.pdf")
    with pytest.raises(ValueError, match="RELEASE_ARTIFACT_MISMATCH"):
        documents.document_qa(model, tmp_path)


def test_svg_long_name_is_preserved_as_wrapped_text_and_edges_follow_source_ids():
    long_name = "LONG" * 50 + "終點"
    row = {
        "match_id": "first",
        "code": "M001",
        "kind": "competitive",
        "round": 1,
        "position": 0,
        "side_a": long_name,
        "side_b": "Other",
        "sources": [],
    }
    final = {
        **row,
        "match_id": "final",
        "code": "M002",
        "round": 2,
        "side_a": "M001 winner",
        "sources": ["first"],
    }
    svg = ElementTree.fromstring(documents.bracket_svg([final, row]))
    texts = [e.text or "" for e in svg.findall("{http://www.w3.org/2000/svg}text")]
    assert long_name in "".join(texts)
    assert len(svg.findall("{http://www.w3.org/2000/svg}path")) == 1
    assert svg.get("height") and float(svg.get("height")) > 200


def test_graphs_paginate_at_readable_physical_size_and_show_continuation_sources(tmp_path):
    snapshot = document_event(5)
    model = documents.view_model(snapshot)
    for index, row in enumerate(model["matches"]):
        if index:
            row["round"] = index // 3 + 1
            row["sources"] = [model["matches"][index - 1]["match_id"]]
    html = documents.render_html(model, tmp_path)
    paths = list(tmp_path.glob("bracket-*.svg"))
    assert len(paths) > 1 and "續前圖" in html
    for path in paths:
        svg = ElementTree.fromstring(path.read_text("utf-8"))
        scale = min(
            (297 - 28) * 96 / 25.4 / float(svg.attrib["width"]),
            (210 - 58) * 96 / 25.4 / float(svg.attrib["height"]),
            1,
        )
        assert 16 * scale * 72 / 96 >= 9


def test_extreme_single_graph_label_fails_instead_of_shrinking_below_nine_points(tmp_path):
    snapshot = document_event()
    snapshot.participants[0].display_name = "長姓名" * 400
    with pytest.raises(ValueError, match="minimum 9pt"):
        documents.render_html(documents.view_model(snapshot), tmp_path)


def test_paper_configuration_applies_to_html_and_excel(tmp_path):
    snapshot = document_event()
    snapshot.config["publication"].update(paper_size="A3", orientation="portrait")
    model = documents.view_model(snapshot)
    html = documents.render_html(model, tmp_path)
    assert "size:A3 portrait" in html
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    wb = load_workbook(tmp_path / "schedule.xlsx")
    assert all(
        ws.page_setup.orientation == "portrait" and str(ws.page_setup.paperSize) == ws.PAPERSIZE_A3
        for ws in wb
    )


def test_qa_rejects_tiny_body_text_even_when_schedule_rows_match(tmp_path):
    snapshot = document_event()
    model = documents.view_model(snapshot)
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    synthetic_pdf(model, tmp_path / "booklet.pdf")
    with pymupdf.open(tmp_path / "booklet.pdf") as doc:
        doc.load_page(0).insert_text((10, 200), "tiny", fontsize=7)
        doc.saveIncr()
    with pytest.raises(ValueError, match="minimum 9pt"):
        documents.document_qa(model, tmp_path)


def test_failed_pdf_release_retains_latest_ready_and_records_failure(tmp_path, monkeypatch):
    snapshot = document_event()
    snapshot.status = "approved"
    snapshot.config["publication"]["layout_template_id"] = "standard-v1"
    monkeypatch.setattr(documents, "_controlled_font", lambda *_: ("", {"mode": "synthetic_test"}))
    repo = SimpleNamespace(root=tmp_path, load=lambda revision_id: snapshot)
    releases = tmp_path / "releases"
    releases.mkdir()
    latest = releases / "latest-ready.json"
    latest.write_text('{"release_id":"previous-approved-release"}', "utf-8")
    before = latest.read_bytes()
    monkeypatch.setattr("squash_scheduler.domain.validation.validate", lambda *_: {"passed": True})

    def fail_pdf(*_args):
        raise RuntimeError("Synthetic renderer failure")

    monkeypatch.setattr(documents, "render_pdf", fail_pdf)
    with pytest.raises(RuntimeError, match="renderer failure"):
        documents.release(repo, snapshot.id)
    assert latest.read_bytes() == before
    assert not list(releases.glob(".pending-*"))
    failures = list(releases.glob("*-failed.json"))
    assert len(failures) == 1 and json.loads(failures[0].read_text("utf-8"))["status"] == "failed"


def test_even_mock_success_stays_qa_pending_until_real_visual_approval(tmp_path, monkeypatch):
    snapshot = document_event()
    snapshot.status = "approved"
    snapshot.config["publication"]["layout_template_id"] = "standard-v1"
    monkeypatch.setattr(documents, "_controlled_font", lambda *_: ("", {"mode": "synthetic_test"}))
    repo = SimpleNamespace(root=tmp_path, load=lambda revision_id: snapshot)
    monkeypatch.setattr("squash_scheduler.domain.validation.validate", lambda *_: {"passed": True})

    def fake_pdf(_html, path):
        synthetic_pdf(documents.view_model(snapshot, draft=False), path)
        return {"renderer": "synthetic-test"}

    monkeypatch.setattr(documents, "render_pdf", fake_pdf)
    release = documents.release(repo, snapshot.id)
    assert release["status"] == "qa_pending"
    manifest = json.loads((Path(release["path"]) / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "qa_pending" and manifest["qa"]["visual_review"] == "required"
    assert not (tmp_path / "releases" / "latest-ready.json").exists()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PDF_SMOKE") != "1",
    reason="Set SQUASH_PDF_SMOKE=1 for installed Edge PDF integration",
)
def test_real_edge_pdf_contains_shared_schedule_and_decodable_qr(tmp_path):
    snapshot = document_event()
    snapshot.config["publication"].update(
        qr_mode="enabled", qr_url="https://example.test/synthetic", qr_label="Synthetic notice"
    )
    manifest = documents.build_documents(snapshot, tmp_path / "real-render", draft=True)
    assert manifest["status"] == "draft"
    assert manifest["qa"]["passed"]
    assert "https://example.test/synthetic" in manifest["qa"]["qr_values"]
    assert (tmp_path / "real-render" / "booklet.pdf").read_bytes().startswith(b"%PDF-")
