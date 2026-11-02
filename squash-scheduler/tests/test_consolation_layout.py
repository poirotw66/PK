"""Placement stages retain their typed winner tree and separate loser sources."""

from xml.etree import ElementTree

import pytest

from squash_scheduler.exporters.reference_layout import _knockout, graph_pages, schedule_cells

NS = "{http://www.w3.org/2000/svg}"


def row(identifier, a, b, *, kind="季殿／名次賽", stage="consolation"):
    def side(value):
        source_type, source_id = value
        identifier_key = "entry_id" if source_type == "entry" else "match_id"
        return {"type": source_type, identifier_key: source_id}

    return {
        "match_id": identifier,
        "code": "CODE-" + identifier,
        "position": int(identifier[1:]),
        "stage_id": stage,
        "division": "女子組",
        "stage": "名次賽",
        "kind": kind,
        "side_a": f"主賽 {a[1]} 敗者" if a[0] == "match_loser" else a[1],
        "side_b": f"主賽 {b[1]} 敗者" if b[0] == "match_loser" else b[1],
        "side_a_ref": side(a),
        "side_b_ref": side(b),
    }


def consolation():
    rows = [
        row(f"c{i + 1}", ("match_loser", f"m{2 * i + 1}"), ("match_loser", f"m{2 * i + 2}"))
        for i in range(4)
    ]
    rows.extend(
        [
            row("c5", ("match_winner", "c1"), ("match_winner", "c2")),
            row("c6", ("match_winner", "c3"), ("match_winner", "c4")),
            row("c7", ("match_winner", "c5"), ("match_winner", "c6")),
        ]
    )
    return rows


def texts(svg):
    root = ElementTree.fromstring(svg)
    return root, ["".join(node.itertext()) for node in root.findall(NS + "text")]


def test_seven_placement_matches_form_complete_winner_tree(tmp_path):
    rows = consolation()
    model = {
        "matches": rows,
        "stages": [
            {
                "id": "consolation",
                "division_id": "d1",
                "order": 2,
                "type": "placement",
                "label": "鼓勵賽",
            }
        ],
    }
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 1
    assert pages[0]["title"] == "女子組　鼓勵賽"
    root, labels = texts(pages[0]["svg"])
    assert len(root.findall(NS + "path")) == 21
    assert pages[0]["match_ids"] == [r["match_id"] for r in rows]
    for match in rows:
        assert labels.count(match["code"]) == 1
    # External loser sources remain complete in the graph or its printed key.
    printed = "".join(labels) + "".join(pages[0]["notes"])
    assert all(f"主賽 m{i} 敗者" in printed for i in range(1, 9))


def test_legacy_standalone_placement_remains_separate():
    match = row("c1", ("match_loser", "m1"), ("match_loser", "m2"))
    root, labels = texts(_knockout([match], "獨立名次賽"))
    assert not root.findall(NS + "path")
    assert sum(match["code"] in label for label in labels) == 1
    assert "主賽 m1 敗者" in "".join(labels)


def test_bronze_stays_below_main_championship_tree():
    rows = [
        row("m1", ("entry", "a"), ("entry", "b"), kind="一般"),
        row("m2", ("entry", "c"), ("entry", "d"), kind="一般"),
        row("m3", ("match_winner", "m1"), ("match_winner", "m2"), kind="一般"),
        row("m4", ("match_loser", "m1"), ("match_loser", "m2")),
    ]
    root, labels = texts(_knockout(rows, "主賽"))
    assert len(root.findall(NS + "path")) == 10
    assert "CODE-m4 季殿" in labels
    assert all(sum(r["code"] in label for label in labels) == 1 for r in rows)


def test_loser_reference_does_not_connect_placement_rounds():
    rows = [
        row("c1", ("entry", "a"), ("entry", "b")),
        row("c2", ("match_loser", "c1"), ("entry", "c")),
    ]
    root, labels = texts(_knockout(rows, "鼓勵賽", placement=True))
    assert len(root.findall(NS + "path")) == 6
    assert "主賽 c1 敗者" in labels
    assert labels.count("CODE-c1") == labels.count("CODE-c2") == 1


def test_placement_forest_rejects_more_than_sixteen_leaves():
    rows = [row(f"c{i}", ("entry", f"a{i}"), ("entry", f"b{i}")) for i in range(1, 10)]
    with pytest.raises(ValueError, match="at most 16"):
        _knockout(rows, "鼓勵賽", placement=True)


def test_sixteen_leaf_placement_forest_keeps_disconnected_matches_separate():
    rows = [row(f"c{i}", ("entry", f"a{i}"), ("entry", f"b{i}")) for i in range(1, 9)]
    root, labels = texts(_knockout(rows, "鼓勵賽", placement=True))
    assert len(root.findall(NS + "path")) == 24
    assert all(labels.count(match["code"]) == 1 for match in rows)


def test_stage_order_and_label_determine_same_division_page_order(tmp_path):
    stages = [
        {"id": name, "division_id": "d1", "order": order, "type": "placement", "label": label}
        for name, order, label in [("b", 2, "B"), ("z", 1, "Z"), ("a", 2, "A")]
    ]
    model = {
        "stages": stages,
        "matches": [
            row(f"c{i}", ("entry", "a"), ("entry", "b"), stage=stage["id"])
            for i, stage in enumerate(stages, 1)
        ],
    }
    pages = graph_pages(model, tmp_path)
    assert [page["stage_id"] for page in pages] == ["z", "a", "b"]


def test_placement_cycles_and_shared_winners_fail_without_inventing_connections():
    rows = consolation()
    rows[-1]["side_a_ref"] = {"type": "match_winner", "match_id": "c7"}
    with pytest.raises(ValueError, match="cycle"):
        _knockout(rows, "鼓勵賽", placement=True)
    rows = consolation()
    rows[-1]["side_a_ref"] = {"type": "match_winner", "match_id": "c6"}
    with pytest.raises(ValueError, match="must be a tree"):
        _knockout(rows, "鼓勵賽", placement=True)


@pytest.mark.parametrize("label", ["鼓勵賽", "名次賽"])
def test_schedule_preserves_typed_placement_stage_name(label):
    match = row("c1", ("entry", "a"), ("entry", "b"))
    match.update(stage=label, stage_type="placement", time="", court="")
    assert schedule_cells(match)[3] == "女子組　" + label
