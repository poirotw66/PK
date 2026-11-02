"""Projection invariants: source graph sides and source table columns coexist."""

import hashlib
import json
from pathlib import Path
from runpy import run_path

import pytest

PROJECT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def model():
    return run_path(str(PROJECT / "examples/historical_booklet_model.py"))["build_model"](
        PROJECT, "projection-test-font"
    )


def test_all_25_graph_stages_and_232_source_rows_project_without_loss(model):
    original = json.loads(
        (PROJECT / "output/full-event-demo/publication-model.json").read_text("utf-8-sig")
    )
    assert len(model["matches"]) == 232
    assert [m["source_schedule_order"] for m in model["matches"]] == list(range(232))
    assert [m["code"] for m in model["matches"]] == [m["code"] for m in original["matches"]]
    assert {m["code"] for m in model["matches"]} == {m["code"] for m in original["matches"]}
    assert [s["source_page"] for s in model["stages"][:25]] == list(range(1, 26))
    assert len(model["source_notes"]) == 36
    originals = {m["code"]: m for m in original["matches"]}
    for row in model["matches"]:
        source = originals[row["code"]]
        assert row["source_schedule_sides"] == [source["side_a"], source["side_b"]]
        assert row["date"] == (source["date"] or "原稿未載")
        assert row["time"] == (source["time"] or "原稿未載")
        assert row["court"] == "原稿未載"
        assert row["source_ref"] == source["source_ref"]


def test_every_source_view_reference_resolves_without_creating_snapshot_identities(model):
    entries = {e["entry_id"] for e in model["source_entries"]}
    rows = {r["match_id"]: r for r in model["matches"]}
    pools = {p["id"]: p for p in model["pools"]}
    stages = {s["id"]: s for s in model["stages"]}
    for row in model["matches"]:
        for key in ("side_a_ref", "side_b_ref"):
            side = row[key]
            # Source-view IDs deliberately remain provenance identifiers; they
            # are not the UUID identities required by an approved Snapshot.
            assert side["type"] in {
                "entry",
                "pool_rank",
                "match_winner",
                "match_loser",
                "draw_slot",
            }
            if side["type"] == "entry":
                assert side["entry_id"] in entries
            elif side["type"] == "pool_rank":
                pool = pools[side["pool_id"]]
                assert 1 <= side["rank"] <= pool["advancement_count"]
                assert stages[pool["stage_id"]]["division_id"] == row["division_id"]
                assert pool["source_page"] < row["source_page"]
            elif side["type"].startswith("match_"):
                assert rows[side["match_id"]]["stage_id"] == row["stage_id"]
            elif side["type"] == "draw_slot":
                assert any(d["id"] == side["draw_run_id"] for d in model["deferrals"])


def test_only_original_c_and_f_slots_use_printed_circled_numerals(model):
    for row in model["matches"]:
        for key in ("side_a", "side_b"):
            side = row[key + "_ref"]
            if side["type"] == "draw_slot":
                if row["source_page"] in {3, 9}:
                    assert row[key] in "①②③④⑤⑥"
                else:
                    assert row[key] == "待定籤位" + side["slot_id"]


def test_girls_match25_graph_orientation_and_schedule_columns_stay_distinct(model):
    row = next(r for r in model["matches"] if r["code"] == "25")
    assert row["side_a"] == "13 勝者" and row["side_b"] == "SyntheticPlayer057"
    assert row["source_schedule_sides"] == ["SyntheticPlayer057", None]
    bronze = next(r for r in model["matches"] if r["code"] == "184")
    assert bronze["kind"] == "季殿／名次賽"
    assert {bronze[key]["type"] for key in ("side_a_ref", "side_b_ref")} == {"match_loser"}


def test_exhibition_rows_form_independent_source_only_appendix(model):
    stage = model["stages"][-1]
    assert stage["label"] == "表演賽（原稿另列）" and stage["independent_matches"]
    matches = [r for r in model["matches"] if r["stage_id"] == stage["id"]]
    assert len(matches) == 2
    assert all(
        not r["sources"] and r["time"] == "原稿未載" and not r["source_graph_exists"]
        for r in matches
    )
    ids = [r[key]["entry_id"] for r in matches for key in ("side_a_ref", "side_b_ref")]
    assert len(set(ids)) == 4  # Repeated display name does not merge source leaves.


def test_unlabelled_edge_reuses_pool_references_without_creating_match(model):
    (edge,) = model["unlabelled_edges"]
    assert edge["stage_id"] == "source:graph:1"
    assert edge["source_ref"]["line_evidence"]["type"] == "line"
    assert edge["official_match_created"] is False
    assert edge["id"] not in {r["match_id"] for r in model["matches"]}
    b_rows = [r for r in model["matches"] if r["stage_id"] == edge["stage_id"]]
    assert len(b_rows) == 9 and "195" in {r["code"] for r in b_rows}
    assert all(edge[key]["type"] == "entry" for key in ("side_a_ref", "side_b_ref"))


def test_source_scope_and_hash_do_not_claim_release_or_full_regulations(model):
    assert model["draft"] and model["evidence"]
    assert model["publication"]["layout_template_id"] == "reference-v2"
    assert model["publication"]["document_profile"] == "order_book"
    assert model["publication"]["font_asset_id"] == "projection-test-font"
    assert model["publication"]["qr_url"] == model["metadata"]["notice_qr_url"]
    assert model["publication"]["sponsors"]
    assert model["participants"] == [] and model["roster"] == [] and model["courts"] == []
    assert model["event_dates"] == ["2026-10-24", "2026-10-25"]
    assert all(
        text in model["coverage_note"]
        for text in (
            "25",
            "232",
            "不推定別名",
            "原稿未載的球場",
            "未編號",
            "不含已核准名冊",
            "Word",
        )
    )
    assert model["metadata"]["roster_scope"]
    assert all(
        model["metadata"][key] is False
        for key in (
            "participant_identities_approved",
            "draw_assignments_approved",
            "schedule_approved",
            "complete_regulations_provided",
        )
    )
    digest = hashlib.sha256(
        json.dumps(
            {k: v for k, v in model.items() if k != "content_hash"},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert model["content_hash"] == digest
