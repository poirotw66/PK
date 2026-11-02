"""Scoring panels and readable reference pages retain every typed match exactly once."""

from collections import Counter
from itertools import combinations
from xml.etree import ElementTree as ET

from test_formats_draws import approve_draw, event

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.exporters import documents
from squash_scheduler.exporters.reference_layout import graph_pages

NS = "{http://www.w3.org/2000/svg}"


def pool_model(count, size):
    pools, rows = [], []
    for p in range(count):
        entries = [{"entry_id": f"e{p}-{i}", "label": f"選手{i}"} for i in range(size)]
        pools.append(
            {"stage_id": "s", "label": chr(65 + p), "entries": entries, "advancement_count": 1}
        )
        for a, b in combinations(entries, 2):
            code = str(len(rows) + 1)
            rows.append(
                {
                    "match_id": code,
                    "code": code,
                    "stage_id": "s",
                    "division_id": "z",
                    "division": "測試組",
                    "stage": "循環賽",
                    "kind": "正式賽",
                    "position": 0,
                    "side_a": a["label"],
                    "side_b": b["label"],
                    "side_a_ref": {"type": "entry", "entry_id": a["entry_id"]},
                    "side_b_ref": {"type": "entry", "entry_id": b["entry_id"]},
                }
            )
    return {
        "stages": [{"id": "s", "division_id": "z", "type": "round_robin", "order": 0}],
        "matches": rows,
        "pools": pools,
    }


def test_six_pools_have_complete_matrices_inside_each_page(tmp_path):
    model = pool_model(6, 3)
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 2
    assert pages[0]["title"].endswith("（1/2）")
    assert pages[1]["title"].endswith("（2/2）")
    assert Counter(mid for page in pages for mid in page["match_ids"]) == Counter(
        row["match_id"] for row in model["matches"]
    )
    for page in pages:
        root = ET.fromstring(page["svg"])
        text = "".join(root.itertext())
        for label in ("積分紀錄", "勝", "敗", "積分", "名次"):
            assert text.count(label) >= 3
        assert len(root.findall(NS + "rect")) == 3 * 4 * 8
        for cell in root.findall(NS + "rect"):
            assert cell.attrib["data-text-padding"] == "1.5"
            assert 0 <= float(cell.attrib["x"]) < 1000
            assert float(cell.attrib["x"]) + float(cell.attrib["width"]) <= 1000
            assert float(cell.attrib["y"]) + float(cell.attrib["height"]) <= 530


def test_large_pools_use_two_panels_and_keep_last_scoring_row_inside_viewbox(tmp_path):
    pages = graph_pages(pool_model(3, 6), tmp_path)
    assert len(pages) == 2
    for page in pages:
        cells = ET.fromstring(page["svg"]).findall(NS + "rect")
        assert max(float(c.attrib["y"]) + float(c.attrib["height"]) for c in cells) <= 530


def test_pool_names_and_reference_legends_stay_with_their_actual_panels(tmp_path):
    model = pool_model(3, 4)
    entry = model["pools"][0]["entries"][0]
    entry["label"] = "SyntheticPlayer047"
    for row in model["matches"]:
        for side in ("side_a", "side_b"):
            if row[side + "_ref"]["entry_id"] == entry["entry_id"]:
                row[side] = entry["label"]
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 2
    first_text = "".join(ET.fromstring(pages[0]["svg"]).itertext())
    last_text = "".join(ET.fromstring(pages[1]["svg"]).itertext())
    assert "A池" in first_text and "B池" in first_text and "C池" in last_text
    assert any("SyntheticPlayer047" in note for note in pages[0]["notes"])
    assert not any("SyntheticPlayer047" in note for note in pages[1]["notes"])
    assert any("不代表種子、抽籤位置或名次" in note for note in pages[1]["notes"])
    # Pool names belong in the heading, away from the crossing diagonal match labels.
    assert not any(
        "".join(text.itertext()) in {"A", "B"}
        for text in ET.fromstring(pages[0]["svg"]).findall(NS + "text")
    )


def test_approved_division_order_and_explicit_encouragement_title(tmp_path):
    model = pool_model(1, 3)
    first = model["stages"][0]
    model["stages"] = [
        first,
        {**first, "id": "s2", "division_id": "a"},
        {**first, "id": "s3", "order": 1, "label": "鼓勵賽"},
    ]
    base = model["matches"][0]
    model["matches"].extend(
        [
            {**base, "stage_id": "s2", "match_id": "a1", "division": "其他組"},
            {**base, "stage_id": "s3", "match_id": "z2"},
        ]
    )
    model["divisions"] = [{"id": "z"}, {"id": "a"}]
    pages = graph_pages(model, tmp_path)
    assert [p["stage_id"] for p in pages] == ["s", "s3", "s2"]
    assert pages[1]["title"].endswith("鼓勵賽")


def test_long_untrusted_names_move_to_paginated_searchable_references(tmp_path):
    model = pool_model(1, 3)
    label = "長姓名<&>" * 250
    model["pools"][0]["entries"][0]["label"] = label
    pages = graph_pages(model, tmp_path)
    assert len(pages) > 2
    assert "參照1" in pages[0]["svg"]
    assert all(not p["match_ids"] for p in pages[1:])
    assert label in "".join(
        "".join(node.itertext())
        for p in pages[1:]
        for node in ET.fromstring(p["svg"]).findall(NS + "text")
    )
    assert "&lt;" in "".join(p["svg"] for p in pages)


def test_single_pool_keeps_normal_english_names_with_numbered_scoring_rows(tmp_path):
    model = pool_model(1, 3)
    model["pools"][0]["entries"][0]["label"] = "SyntheticPlayer006"
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 1
    assert "SyntheticPlayer006" in "".join(ET.fromstring(pages[0]["svg"]).itertext())


def test_short_multi_pool_reference_key_stays_on_its_graph_page(tmp_path):
    model = pool_model(3, 3)
    model["pools"][0]["entries"][0]["label"] = "SyntheticPlayer028"
    model["pools"][1]["entries"][0]["label"] = "SyntheticPlayer047"
    pages = graph_pages(model, tmp_path)
    assert len(pages) == 1
    assert "參照1：SyntheticPlayer028" in pages[0]["notes"]
    assert "參照2：SyntheticPlayer047" in pages[0]["notes"]


def test_single_pool_long_names_remain_separate_from_match_numbers(tmp_path):
    def text_rectangles(node):
        size = int(node.attrib["font-size"])
        x, y = float(node.attrib["x"]), float(node.attrib["y"])
        for span in node.findall(NS + "tspan"):
            y += float(span.attrib["dy"])
            width = sum(2 if ord(c) > 255 else 1 for c in (span.text or "")) * size / 2
            start = (
                x - width
                if node.attrib["text-anchor"] == "end"
                else (x - width / 2 if node.attrib["text-anchor"] == "middle" else x)
            )
            yield start, y - size, start + width, y + 3

    for size in (5, 6):
        model = pool_model(1, size)
        for entry in model["pools"][0]["entries"]:
            entry["label"] = "SyntheticPlayer006"
        pages = graph_pages(model, tmp_path)
        assert len(pages) == 1
        nodes = ET.fromstring(pages[0]["svg"]).findall(NS + "text")
        names = [n for n in nodes if n.attrib["font-size"] == "20"]
        codes = [
            n for n in nodes if n.attrib["font-size"] == "18" and "".join(n.itertext()).isdigit()
        ]
        for name in names:
            assert "SyntheticPlayer006" in "".join(name.itertext())
            for a in text_rectangles(name):
                assert a[0] >= 0
                table_left = 785 - (84 + (size + 4) * 32) / 2
                assert a[2] + 8 < table_left
                for code in codes:
                    for b in text_rectangles(code):
                        dx = max(a[0] - b[2], b[0] - a[2], 0)
                        dy = max(a[1] - b[3], b[1] - a[3], 0)
                        assert dx * dx + dy * dy >= 8 * 8


def test_graph_filenames_sort_in_printed_order_after_nine_pages(tmp_path):
    pages = graph_pages(pool_model(31, 3), tmp_path)
    filenames = [p["file"] for p in pages]
    assert len(filenames) == 11
    assert filenames == sorted(filenames)
    assert filenames[0] == "bracket-reference-001.svg"


def test_six_player_three_digit_match_numbers_have_separate_label_boxes(tmp_path):
    model = pool_model(1, 6)
    for i, row in enumerate(model["matches"], 101):
        row["code"] = str(i)
    pages = graph_pages(model, tmp_path)
    root = ET.fromstring(pages[0]["svg"])
    assert len(root.findall(NS + "path")) == 15
    numbers = [
        node
        for node in root.findall(NS + "text")
        if node.attrib["font-size"] == "18" and "".join(node.itertext()).isdigit()
    ]
    assert {"".join(node.itertext()) for node in numbers} == {str(i) for i in range(101, 116)}
    for a, b in combinations(numbers, 2):
        ax, ay = float(a.attrib["x"]), float(a.attrib["y"])
        bx, by = float(b.attrib["x"]), float(b.attrib["y"])
        dx = max(abs(ax - bx) - 27, 0)  # Three digits at an 18px font.
        dy = max(abs(ay - by) - 21, 0)
        assert dx * dx + dy * dy >= 6 * 6


def test_dense_long_code_layout_falls_back_without_omitting_a_match(tmp_path):
    model = pool_model(1, 6)
    for i, row in enumerate(model["matches"], 101):
        row["code"] = "VERY-LONG-MATCH-" + str(i)
    pages = graph_pages(model, tmp_path)
    root = ET.fromstring(pages[0]["svg"])
    assert "循環積分與場號對照" in "".join(root.itertext())
    for row in model["matches"]:
        assert row["code"] in "".join(root.itertext())
    assert len(pages[0]["match_ids"]) == 15


def test_sixteen_entry_bracket_keeps_every_match_and_uses_readable_leaf_spacing(tmp_path):
    snapshot, division = event(16, "single_elimination")
    approve_draw(run_draw(snapshot, division.id, 77, "tester"))
    generate_matches(snapshot)
    for i, match in enumerate(snapshot.matches, 1):
        match.display_code = str(i)
    pages = graph_pages(documents.view_model(snapshot), tmp_path)
    graphs = [p for p in pages if p["match_ids"]]
    assert len(graphs) == 1
    assert len(graphs[0]["match_ids"]) == 15
    root = ET.fromstring(graphs[0]["svg"])
    assert len(root.findall(NS + "path")) == 45
    assert all(0 <= float(t.attrib["y"]) <= 530 for t in root.findall(NS + "text"))


def test_draw_notes_come_only_from_referenced_actual_declaration(tmp_path):
    model = pool_model(1, 3)
    model["stages"][0]["type"] = "elimination"
    model["matches"] = [model["matches"][0]]
    model["matches"][0]["side_a_ref"] = {"type": "draw_slot", "draw_run_id": "draw1"}
    model["deferrals"] = [
        {
            "id": "draw1",
            "status": "declared",
            "procedure": {
                "description": "來源確認後公開抽籤",
                "duration_minutes": 7,
                "execution_trigger": "after_sources_confirmed",
                "earliest_at": None,
            },
        },
        {"id": "other", "procedure": {"description": "不可出現"}},
    ]
    notes = "".join(graph_pages(model, tmp_path)[0]["notes"])
    assert "來源確認後公開抽籤" in notes
    assert "7 分鐘" in notes
    assert "declared" in notes
    assert "不可出現" not in notes
