"""Reference graphs keep typed identities and mixed printable page geometry."""

from __future__ import annotations

import os
from xml.etree import ElementTree

import pymupdf
import pytest
from test_documents import document_event
from test_formats_draws import approve_draw, event

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.exporters import documents
from squash_scheduler.exporters.reference_layout import graph_pages, schedule_cells, schedule_pages


def pool_event():
    snapshot, division = event(9, "pools_3x3_top2_knockout6_bronze")
    division.format_plan.update(
        pool_sizes=[3, 3, 3],
        advancement_counts=[2, 2, 2],
        bracket_capacity=8,
        placement_matches=["bronze"],
    )
    approve_draw(run_draw(snapshot, division.id, 77, "tester"))
    generate_matches(snapshot)
    for index, match in enumerate(snapshot.matches, 1):
        match.display_code = str(index)
    snapshot.config.update(
        event={"name": "Synthetic", "venue_name": "Venue", "timezone": "Asia/Taipei"},
        publication={
            "layout_template_id": "reference-v1",
            "qr_mode": "disabled",
            "sponsor_mode": "none",
        },
    )
    return snapshot


def test_three_pools_share_one_sheet_and_knockout_keeps_all_match_codes(tmp_path):
    snapshot = pool_event()
    model = documents.view_model(snapshot)
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 2
    ns = "{http://www.w3.org/2000/svg}"
    first = ElementTree.fromstring(pages[0]["svg"])
    assert len(first.findall(ns + "path")) == 9
    assert all(len(pool["entries"]) == 3 for pool in model["pools"])
    second = ElementTree.fromstring(pages[1]["svg"])
    text = "".join(second.itertext())
    assert "季殿" in text
    for row in (row for row in model["matches"] if row["stage"] == "淘汰賽"):
        assert row["code"] in text
    labels = {
        row["round"]: row["round_label"]
        for row in model["matches"]
        if row["kind"] == "正式賽" and row["stage_type"] == "elimination"
    }
    assert labels == {1: "八強", 2: "四強", 3: "冠亞"}
    assert all(
        schedule_cells(row, classic=True)[2].endswith(row["round_label"])
        for row in model["matches"]
        if "round_label" in row
    )


def test_untrusted_labels_are_escaped_and_same_names_keep_distinct_vertices(tmp_path):
    snapshot = pool_event()
    for person in snapshot.participants:
        person.display_name = "Same <script>"
    model = documents.view_model(snapshot)
    html = documents.render_html(model, tmp_path)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert len({entry["entry_id"] for pool in model["pools"] for entry in pool["entries"]}) == 9
    assert (
        sum(
            len(
                ElementTree.fromstring(path.read_text("utf-8")).findall(
                    "{http://www.w3.org/2000/svg}path"
                )
            )
            for path in tmp_path.glob("*.svg")
        )
        == 25
    )


def test_reference_has_landscape_graphs_portrait_schedule_and_merged_player_header(tmp_path):
    model = documents.view_model(pool_event())
    html = documents.render_html(model, tmp_path)
    assert "@page graph {size:A4 landscape" in html
    assert "@page schedule {size:A4 portrait" in html
    assert '<th colspan="2">選手</th>' in html
    assert model["template"]["id"] == "reference-v1"


def test_schedule_pages_split_by_date_and_wrap_capacity():
    model = documents.view_model(document_event())
    base = model["matches"][0]
    model["matches"] = [
        {
            **base,
            "match_id": str(i),
            "code": str(i),
            "date": "2027-01-02" if i < 30 else "2027-01-03",
        }
        for i in range(35)
    ]
    pages = schedule_pages(model)
    assert len(pages) >= 3
    assert sum(len(page["rows"]) for page in pages) == 35
    assert all(len(page["rows"]) <= 26 for page in pages)
    assert pages[-1]["date"] == "2027-01-03"


def test_historical_round_and_generic_draw_slot_remain_visible_without_changing_source():
    model = documents.view_model(document_event())
    row = {**model["matches"][0], "stage": "冠亞", "side_a": "①", "side_b": "③"}
    cells = schedule_cells(row)
    assert "冠亞" in cells[3]
    assert cells[4:] == ["圈1", "圈3"]
    assert (row["side_a"], row["side_b"]) == ("①", "③")


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("SQUASH_PDF_SMOKE") != "1", reason="Installed Edge PDF check")
@pytest.mark.parametrize("template", ["reference-v1", "reference-v2"])
def test_reference_pdf_has_mixed_orientation_and_detects_swapped_opponents(tmp_path, template):
    snapshot = document_event()
    snapshot.config["publication"]["layout_template_id"] = template
    model = documents.view_model(snapshot)
    html = documents.render_html(model, tmp_path)
    documents.export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    html_path = tmp_path / "review.html" if template == "reference-v2" else None
    if html_path is not None:
        html_path.write_text(html, "utf-8")
    documents.render_pdf(html, tmp_path / "booklet.pdf", html_path=html_path)
    assert documents.document_qa(model, tmp_path)["passed"]
    with pymupdf.open(tmp_path / "booklet.pdf") as pdf:
        assert any(p.rect.width > p.rect.height for p in pdf)
        assert any(p.rect.width < p.rect.height for p in pdf)
        if template == "reference-v2":
            schedule = next(p for p in pdf if "預計時間" in p.get_text().splitlines())
            text = schedule.get_text().splitlines()
            assert "球場" not in text
            assert model["matches"][0]["code"] in text
    changed = {**model, "matches": [dict(row) for row in model["matches"]]}
    changed["matches"][0]["side_a"], changed["matches"][0]["side_b"] = (
        changed["matches"][0]["side_b"],
        changed["matches"][0]["side_a"],
    )
    documents.export_excel(snapshot, changed, tmp_path / "schedule.xlsx")
    with pytest.raises(ValueError, match="schedule row"):
        documents.document_qa(changed, tmp_path)
