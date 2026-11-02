"""Fresh-source checks and mutation checks for printed historical topology."""

from copy import deepcopy
from pathlib import Path
from runpy import run_path

import pymupdf
import pytest

HELPER = run_path(str(Path(__file__).parents[1] / "examples/historical_graph_catalog.py"))


@pytest.fixture(scope="module")
def catalog():
    return HELPER["build_catalog"]()


def test_all_25_graphs_exactly_cover_all_230_numbered_schedule_rows(catalog):
    assert [g["source_page"] for g in catalog["graphs"]] == list(range(1, 26))
    assert catalog["verification"]["schedule_row_clips"] == 230
    assert catalog["verification"]["numbered_codes"] == list(map(str, range(1, 231)))
    assert len(catalog["verification"]["schedule_only_unnumbered_rows"]) == 2
    source = next((Path(__file__).parents[2]).glob("*20261023.pdf"))
    with pymupdf.open(source) as pdf:
        for graph in catalog["graphs"]:
            for match in graph["matches"]:
                ref = match["number_evidence"]
                assert (
                    pdf[ref["page"] - 1].get_text(clip=pymupdf.Rect(ref["bbox"])).strip()
                    == match["code"]
                )


def test_delivered_catalog_readiness_uses_the_validated_page_map(catalog):
    original = deepcopy(catalog)
    mapping = {str(number): [number + 4] for number in range(1, 26)}
    delivered = HELPER["with_generation_evidence"](catalog, mapping, 48)
    assert delivered["readiness"]["remaining_historical_generation_pages"] == []
    assert delivered["readiness"]["status_scope"] == "validated_delivered_pdf"
    assert all(
        g["generated_pdf_pages"] == mapping[str(g["source_page"])] for g in delivered["graphs"]
    )
    assert not any(d["historical_sample_missing_pages"] for d in delivered["division_readiness"])
    assert catalog == original
    assert 'href="graph-catalog.json"' in HELPER["render_html"](
        delivered, json_filename="graph-catalog.json"
    )


@pytest.mark.parametrize("failure", ["missing", "empty", "duplicate", "outside"])
def test_delivered_catalog_rejects_unproven_generation(failure, catalog):
    mapping = {str(number): [number + 4] for number in range(1, 26)}
    if failure == "missing":
        del mapping["25"]
    elif failure == "empty":
        mapping["25"] = []
    elif failure == "duplicate":
        mapping["25"] = mapping["1"]
    else:
        mapping["25"] = [49]
    with pytest.raises(ValueError, match="source page mapping"):
        HELPER["with_generation_evidence"](catalog, mapping, 48)


def test_swapped_semifinal_branch_rejected_by_printed_geometry(catalog):
    graph = deepcopy(catalog["graphs"][8])
    match = next(m for m in graph["matches"] if m["code"] == "204")
    match["sides"][1]["match_code"] = "186"
    with pytest.raises(ValueError, match="Nonconnecting branch"):
        HELPER["validate_topology"](graph)


def test_erasing_printed_stem_rejects_formerly_valid_dependency(catalog):
    graph = deepcopy(catalog["graphs"][18])
    evidence = next(e for e in graph["connection_evidence"] if e["from_code"] == "142")
    stems = evidence["stem_bboxes"]
    graph["drawing_primitives"] = [
        p for p in graph["drawing_primitives"] if p.get("bbox") not in stems
    ]
    with pytest.raises(ValueError, match="continuous printed stem"):
        HELPER["validate_topology"](graph)


def test_bronze_requires_independent_schedule_semantics(catalog):
    graph = deepcopy(catalog["graphs"][19])
    bronze = next(m for m in graph["matches"] if m["code"] == "184")
    bronze["schedule_stage_label"] = "決賽"
    with pytest.raises(ValueError, match="bronze semantics"):
        HELPER["validate_topology"](graph)


def test_encouragement_internal_connections_known_but_entrants_unassigned(catalog):
    for index, expected in [(18, 6), (20, 3)]:
        graph = catalog["graphs"][index]
        assert len(graph["connection_evidence"]) == expected
        assert all(
            side["type"] in {"match_winner", "draw_slot"}
            for m in graph["matches"]
            for side in m["sides"]
        )
        assert all(
            side.get("draw_run_id") == graph["id"] + ":unassigned-leaves"
            for m in graph["matches"]
            for side in m["sides"]
            if side["type"] == "draw_slot"
        )


def test_b_group_195_present_and_unlabelled_edge_never_fabricates_match(catalog):
    graph = catalog["graphs"][0]
    row = next(m for m in graph["matches"] if m["code"] == "195")
    assert [s["label"] for s in row["sides"]] == ["SyntheticPlayer003", "SyntheticPlayer002"]
    (edge,) = graph["unlabelled_edges"]
    assert edge["code"] is None and edge["schedule_match_id"] is None
    assert [s["label"] for s in edge["sides"]] == ["SyntheticAlias007", "SyntheticPlayer002"]
    assert len(graph["matches"]) + len(graph["unlabelled_edges"]) == 10


def test_pool_advancement_and_rank_constraints_preserved(catalog):
    assert [(p["entry_count"], p["advancement_count"]) for p in catalog["graphs"][5]["pools"]] == [
        (4, 2)
    ] + [(3, 1)] * 5
    assert catalog["graphs"][2]["draw_constraints"]["first_place_allowed_slots"] == ["1", "3", "6"]
    assert catalog["graphs"][8]["draw_constraints"]["actual_assignments_known"] is False
    # The thirteen-player girls draw has three source-printed byes. Preserve
    # the right-hand SyntheticPlayer057 leaf of match 25 even though the schedule lists it first.
    m = next(m for m in catalog["graphs"][19]["matches"] if m["code"] == "25")
    assert m["sides"][0]["match_code"] == "13"
    assert m["sides"][1]["label"] == "SyntheticPlayer057"


def test_round_robin_pair_must_have_its_code_near_the_actual_printed_edge(catalog):
    graph = deepcopy(catalog["graphs"][0])
    pool = graph["pools"][0]
    match = next(m for m in graph["matches"] if m["code"] == "94")
    SyntheticAlias007 = next(e for e in pool["entries"] if e["label"] == "SyntheticAlias007")
    match["sides"][1]["entry_id"] = SyntheticAlias007["entry_id"]
    source = next(Path(__file__).parents[2].glob("*20261023.pdf"))
    with (
        pymupdf.open(source) as pdf,
        pytest.raises(ValueError, match="code detached from declared edge"),
    ):
        HELPER["pool_edge_evidence"](pdf[0], pool, graph["matches"])
