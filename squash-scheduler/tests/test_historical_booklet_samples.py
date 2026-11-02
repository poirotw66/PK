"""Small source-label fixtures verify printed trees and unassigned entrant scope."""

import importlib.util
from pathlib import Path

import pytest

from squash_scheduler.exporters.reference_layout import graph_pages

SCRIPT = Path(__file__).resolve().parents[1] / "examples/historical_booklet_samples.py"
SPEC = importlib.util.spec_from_file_location("historical_booklet_samples", SCRIPT)
samples = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(samples)


def test_notice_uses_fresh_source_text_and_locally_decoded_historical_qr():
    project = SCRIPT.parents[1]
    if not (project / "output/full-event-demo/publication-model.json").exists() or not list(
        project.parent.glob("*20261023.pdf")
    ):
        pytest.skip("Historical source PDF and extracted model are local review inputs")
    model = samples.build_model(project)
    publication = model["publication"]
    assert model["draft"] is True
    assert publication["qr_mode"] == "enabled"
    assert publication["qr_url"] == "https://squashmatches.web.app/"
    assert publication["qr_label"] == "中正盃壁球錦標賽即時場次通知"
    assert publication["sponsor_mode"] == "provided"
    assert publication["sponsors"] == [{"display_name": "感謝 SyntheticAlias008", "text": "設計贊助"}]
    evidence = next(note for note in model["source_notes"] if note["source_page"] == 36)
    text = "".join(evidence["extracted_text"].split())
    assert "感謝SyntheticAlias008設計贊助" in text
    assert publication["qr_label"] in text
    assert 36 in model["metadata"]["source_pages"]
    assert model["metadata"]["notice_qr_url"] == publication["qr_url"]
    assert model["metadata"]["notice_sponsor_scope"] == "historical_source_sample_only"
    match_195 = next(row for row in model["matches"] if row["code"] == "195")
    assert match_195["division"] == "男子B組"
    assert (match_195["side_a"], match_195["side_b"]) == ("SyntheticPlayer003", "SyntheticPlayer002")
    assert match_195["source_ref"]["page"] == 34
    notes = "".join(line for summary in model["rule_summaries"] for line in summary["lines"])
    assert "第195場未列" not in notes
    assert "SyntheticAlias007對SyntheticPlayer002連線未編號" in notes


def fixture_sides():
    # Same display text deliberately occurs twice: this cannot establish identity.
    originals = {str(code): {"side_a": "Same", "side_b": "Same"} for code in range(1, 9)}
    return samples.school_printed_sides(originals)


def leaves(sides, code):
    found = []
    for _, ref in sides[code]:
        if ref["type"] == "match_winner":
            found.extend(leaves(sides, ref["match_id"].rsplit(":", 1)[1]))
        else:
            found.append(ref)
    return found


def test_main_printed_tree_covers_sixteen_source_positions_and_separate_bronze():
    sides = fixture_sides()
    positions = leaves(sides, "181")
    assert len(positions) == 16
    assert all(ref["type"] == "entry" for ref in positions)
    assert len({ref["entry_id"] for ref in positions}) == 16
    assert [ref["match_id"] for _, ref in sides["181"]] == ["source:match:171", "source:match:172"]
    assert [ref["match_id"] for _, ref in sides["182"]] == ["source:match:171", "source:match:172"]
    assert all(ref["type"] == "match_loser" for _, ref in sides["182"])


def test_encouragement_internal_tree_has_eight_unknown_slots_and_no_main_sources():
    sides = fixture_sides()
    encouragement = {"30", "31", "32", "33", "141", "142", "151"}
    positions = leaves(sides, "151")
    assert len(positions) == 8
    assert all(ref["type"] == "draw_slot" for ref in positions)
    assert {ref["slot_id"] for ref in positions} == set(map(str, range(1, 9)))
    assert all("entry_id" not in ref and "participant_id" not in ref for ref in positions)
    assert all(
        ref["type"] == "match_winner" and ref["match_id"].rsplit(":", 1)[1] in encouragement
        for code in ("141", "142", "151")
        for _, ref in sides[code]
    )


def test_main_and_encouragement_render_as_two_complete_graph_pages(tmp_path):
    sides = fixture_sides()
    encouragement = {"30", "31", "32", "33", "141", "142", "151"}
    rows = []
    for index, (code, pair) in enumerate(sides.items()):
        a, b = pair
        rows.append(
            {
                "match_id": "source:match:" + code,
                "code": code,
                "division_id": "school",
                "division": "School",
                "stage_id": "encouragement" if code in encouragement else "main",
                "stage": "Encouragement" if code in encouragement else "Main",
                "kind": "季殿／名次賽" if code == "182" else "source",
                "position": index,
                "side_a": a[0],
                "side_a_ref": a[1],
                "side_b": b[0],
                "side_b_ref": b[1],
            }
        )
    model = {
        "matches": rows,
        "pools": [],
        "stages": [
            {"id": "main", "division_id": "school", "type": "elimination", "order": 0},
            {"id": "encouragement", "division_id": "school", "type": "elimination", "order": 1},
        ],
    }
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 2
    assert set(pages[0]["match_ids"]) == {
        "source:match:" + code for code in sides if code not in encouragement
    }
    assert set(pages[1]["match_ids"]) == {"source:match:" + code for code in encouragement}
    assert "182" in pages[0]["svg"]
    assert "151" in pages[1]["svg"]
