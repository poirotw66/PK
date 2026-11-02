"""An unnumbered source line is geometry, never an invented official match."""

from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest
from test_reference_graph_panels import NS, pool_model

from squash_scheduler.exporters.reference_layout import graph_pages


def source_model(size=3):
    model = pool_model(1, size)
    omitted = model["matches"].pop()
    edge = {
        "stage_id": "s",
        "side_a_ref": omitted["side_a_ref"],
        "side_b_ref": omitted["side_b_ref"],
        "source_ref": {"page": 1, "evidence": "printed line without a match number"},
    }
    model["unlabelled_edges"] = [edge]
    return model


def test_three_player_source_keeps_unnumbered_third_line_without_creating_match(tmp_path):
    model = source_model()
    before = deepcopy(model)
    page = graph_pages(model, tmp_path)[0]
    root = ET.fromstring(page["svg"])
    paths = root.findall(NS + "path")
    assert len(paths) == 3
    source = [path for path in paths if path.attrib.get("data-source-only") == "true"]
    assert len(source) == 1
    assert "data-match-id" not in source[0].attrib
    assert len([path for path in paths if "data-match-id" in path.attrib]) == 2
    assert page["match_ids"] == [row["match_id"] for row in model["matches"]]
    assert page["unlabelled_edge_count"] == 1
    assert "原稿有未編號連線，未建立正式場次" in page["notes"]
    assert model == before


def test_five_player_mens_b_keeps_all_nine_codes_including_195_and_tenth_source_line(tmp_path):
    # Transcribed source B pairs: 195 is Su/Wu; only SyntheticAlias007/Wu has no number.
    pairs = [
        ("94", "SyntheticPlayer004", "SyntheticPlayer005"),
        ("95", "SyntheticPlayer003", "SyntheticAlias007"),
        ("110", "SyntheticPlayer004", "SyntheticAlias007"),
        ("111", "SyntheticPlayer005", "SyntheticPlayer003"),
        ("126", "SyntheticPlayer005", "SyntheticAlias007"),
        ("195", "SyntheticPlayer003", "SyntheticPlayer002"),
        ("214", "SyntheticPlayer004", "SyntheticPlayer003"),
        ("215", "SyntheticPlayer005", "SyntheticPlayer002"),
        ("230", "SyntheticPlayer004", "SyntheticPlayer002"),
    ]
    model = pool_model(1, 5)
    labels = list(dict.fromkeys(name for _, first, second in pairs for name in (first, second)))
    for entry, label in zip(model["pools"][0]["entries"], labels, strict=True):
        entry["label"] = label
    refs = {
        entry["label"]: {"type": "entry", "entry_id": entry["entry_id"]}
        for entry in model["pools"][0]["entries"]
    }
    base = model["matches"][0]
    model["matches"] = [
        {
            **base,
            "match_id": "source:match:" + code,
            "code": code,
            "side_a": first,
            "side_b": second,
            "side_a_ref": refs[first],
            "side_b_ref": refs[second],
        }
        for code, first, second in pairs
    ]
    model["unlabelled_edges"] = [
        {
            "stage_id": "s",
            "side_a_ref": refs["SyntheticAlias007"],
            "side_b_ref": refs["SyntheticPlayer002"],
            "source_ref": {"page": 1, "evidence": "right lower pentagon edge has no number"},
        }
    ]
    before = deepcopy(model)
    page = graph_pages(model, tmp_path)[0]
    root = ET.fromstring(page["svg"])
    paths = root.findall(NS + "path")
    assert len(paths) == 10
    assert len([path for path in paths if path.attrib.get("data-source-only") == "true"]) == 1
    assert len([path for path in paths if "data-match-id" in path.attrib]) == 9
    assert set(page["match_ids"]) == {"source:match:" + code for code, _, _ in pairs}
    assert "195" in "".join(root.itertext())
    assert page["unlabelled_edge_count"] == 1
    assert model == before


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda m: m["unlabelled_edges"][0].update(stage_id="unknown"), "stage"),
        (lambda m: m["stages"][0].update(type="elimination"), "stage"),
        (lambda m: m["unlabelled_edges"][0]["side_a_ref"].update(entry_id="unknown"), "pool"),
        (lambda m: m["unlabelled_edges"][0]["side_a_ref"].update(type="draw_slot"), "typed"),
        (
            lambda m: m["unlabelled_edges"][0].update(
                side_b_ref=m["unlabelled_edges"][0]["side_a_ref"]
            ),
            "self-pair",
        ),
        (
            lambda m: m["unlabelled_edges"][0].update(
                side_a_ref=m["matches"][0]["side_a_ref"],
                side_b_ref=m["matches"][0]["side_b_ref"],
            ),
            "duplicate",
        ),
        (lambda m: m["unlabelled_edges"].append(deepcopy(m["unlabelled_edges"][0])), "duplicate"),
        (lambda m: m["unlabelled_edges"][0].pop("source_ref"), "provenance"),
    ],
)
def test_invalid_source_edges_are_rejected_before_writing_any_graph(tmp_path, change, reason):
    model = source_model()
    change(model)
    with pytest.raises(ValueError, match="SOURCE_EDGE_INVALID:.*" + reason):
        graph_pages(model, tmp_path)
    assert not list(tmp_path.glob("*.svg"))


def test_cross_pool_source_edge_is_rejected_even_if_both_entries_exist(tmp_path):
    model = pool_model(2, 3)
    model["unlabelled_edges"] = [
        {
            "stage_id": "s",
            "side_a_ref": {"type": "entry", "entry_id": "e0-0"},
            "side_b_ref": {"type": "entry", "entry_id": "e1-0"},
            "source_ref": {"page": 1},
        }
    ]
    with pytest.raises(ValueError, match="same unique pool"):
        graph_pages(model, tmp_path)


def test_without_source_edges_an_incomplete_pool_does_not_gain_automatic_lines(tmp_path):
    model = source_model()
    model.pop("unlabelled_edges")
    page = graph_pages(model, tmp_path)[0]
    assert len(ET.fromstring(page["svg"]).findall(NS + "path")) == 2
    assert page["unlabelled_edge_count"] == 0
    assert "原稿有未編號連線，未建立正式場次" not in page["notes"]


def test_source_line_and_note_belong_only_to_the_correct_pool_page(tmp_path):
    model = pool_model(4, 3)
    omitted = model["matches"].pop()
    model["unlabelled_edges"] = [
        {
            "stage_id": "s",
            "side_a_ref": omitted["side_a_ref"],
            "side_b_ref": omitted["side_b_ref"],
            "source_ref": {"page": 8},
        }
    ]
    pages = graph_pages(model, tmp_path)
    assert [page["unlabelled_edge_count"] for page in pages] == [0, 1]
    assert "原稿有未編號連線，未建立正式場次" not in pages[0]["notes"]
    assert "原稿有未編號連線，未建立正式場次" in pages[1]["notes"]
    assert sum(len(page["match_ids"]) for page in pages) == 11
