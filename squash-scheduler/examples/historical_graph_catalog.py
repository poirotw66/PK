"""Source-only typed graph catalogue for all 25 historical PDF diagrams.

Run with .venv/Scripts/python.exe -X utf8 examples/historical_graph_catalog.py.
Labels are local source displays, never approved participant identities or draws.
All printed connections are transcribed; blank initial leaves remain draw slots.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from html import escape
from pathlib import Path

import pymupdf

PROJECT = Path(__file__).resolve().parents[1]

# Each pool is explicitly a connected printed polygon. Labels without a printed
# letter are positional source identifiers, not an inferred official pool name.
POOL_SPECS = {
    1: [("", 5, None, [94, 110, 126, 214, 230, 215, 95, 111, 195])],
    2: [("", 3, 2, [96, 112, 127]), ("", 3, 2, [97, 113, 128]), ("", 3, 2, [98, 114, 129])],
    4: [
        ("A", 4, 2, [99, 100, 115, 116, 130, 131]),
        ("B", 4, 2, [101, 102, 117, 118, 132, 133]),
        ("", 3, 1, [103, 119, 134]),
        ("", 3, 1, [104, 120, 135]),
    ],
    6: [
        ("A", 4, 2, [37, 38, 56, 57, 75, 76]),
        ("", 3, 1, [39, 58, 77]),
        ("", 3, 1, [40, 59, 78]),
        ("", 3, 1, [41, 60, 79]),
        ("", 3, 1, [42, 61, 80]),
        ("", 3, 1, [43, 62, 81]),
    ],
    8: [("", 3, 2, [44, 63, 82]), ("", 3, 2, [45, 64, 83]), ("", 3, 2, [46, 65, 84])],
    10: [
        ("A", 4, 2, [105, 106, 121, 122, 136, 137]),
        ("", 3, 1, [107, 123, 138]),
        ("", 3, 1, [108, 124, 139]),
        ("", 3, 1, [109, 125, 140]),
    ],
    12: [
        ("A", 4, 2, [47, 48, 66, 67, 85, 86]),
        ("B", 4, 2, [49, 50, 68, 69, 87, 88]),
        ("", 3, 2, [51, 70, 89]),
    ],
    14: [
        ("", 3, 1, [52, 71, 90]),
        ("", 3, 1, [53, 72, 91]),
        ("", 3, 1, [54, 73, 92]),
        ("", 3, 1, [55, 74, 93]),
    ],
    16: [("", 4, None, [14, 15, 26, 27, 159, 160])],
    17: [("", 5, None, [16, 17, 28, 29, 161, 162, 169, 170, 179, 180])],
    22: [("A", 4, 2, [145, 146, 153, 154, 163, 164]), ("B", 4, 2, [147, 148, 155, 156, 165, 166])],
    24: [("A", 3, 2, [149, 157, 167]), ("B", 3, 2, [150, 158, 168])],
}


def winner(code):
    return {"type": "match_winner", "match_code": str(code)}


def loser(code):
    return {"type": "match_loser", "match_code": str(code)}


def slot(number):
    return {"type": "draw_slot", "slot_id": str(number)}


def rank(pool, number):
    return {"type": "pool_rank", "pool_label": pool, "rank": number}


def display(label):
    return {"type": "source_display", "label": label}


def bracket_specs():
    specs = {}
    # Six entrant, two first-round byes; explicit printed leaves may be ranks.
    for page, codes, leaves in [
        (3, [193, 194, 212, 213, 228, 229], [slot(i) for i in range(1, 7)]),
        (
            5,
            [191, 192, 210, 211, 226, 227],
            [rank("A", 1), slot(2), rank("B", 2), rank("A", 2), slot(5), rank("B", 1)],
        ),
        (9, [185, 186, 204, 205, 220, 221], [slot(i) for i in range(1, 7)]),
        (
            13,
            [35, 36, 177, 178, 202, 203],
            [rank("A", 1), slot(2), rank("B", 2), rank("A", 2), slot(5), rank("B", 1)],
        ),
    ]:
        a, b, c, d, f, z = codes
        specs[page] = {
            a: (leaves[1], leaves[2]),
            b: (leaves[3], leaves[4]),
            c: (leaves[0], winner(a)),
            d: (winner(b), leaves[5]),
            f: (winner(c), winner(d)),
            z: (loser(c), loser(d)),
        }
    specs[7] = {
        187: (slot(2), slot(3)),
        188: (slot(4), slot(5)),
        189: (slot(6), rank("A", 2)),
        206: (rank("A", 1), winner(187)),
        207: (winner(188), winner(189)),
        222: (winner(206), winner(207)),
        223: (loser(206), loser(207)),
    }
    for page, codes in [(11, [190, 208, 209, 224, 225]), (21, [34, 143, 144, 152, None])]:
        a, b, c, f, z = codes
        leaves = (
            [rank("A", 1), slot(2), slot(3), slot(4), rank("A", 2)]
            if page == 11
            else [slot(i) for i in range(1, 6)]
        )
        specs[page] = {
            a: (leaves[2], leaves[3]),
            b: (leaves[0], leaves[1]),
            c: (winner(a), leaves[4]),
            f: (winner(b), winner(c)),
        }
        if z:
            specs[page][z] = (loser(b), loser(c))
    for page, codes, leaves in [
        (15, [175, 176, 200, 201], [slot(i) for i in range(1, 5)]),
        (23, [196, 197, 216, 217], [rank("A", 1), rank("B", 2), rank("A", 2), rank("B", 1)]),
        (25, [198, 199, 218, 219], [rank("A", 1), rank("B", 2), rank("A", 2), rank("B", 1)]),
    ]:
        a, b, f, z = codes
        specs[page] = {
            a: tuple(leaves[:2]),
            b: tuple(leaves[2:]),
            f: (winner(a), winner(b)),
            z: (loser(a), loser(b)),
        }
    specs[18] = {i: (None, None) for i in range(1, 9)}
    for i in range(4):
        specs[18][18 + i] = (winner(1 + i * 2), winner(2 + i * 2))
    specs[18].update(
        {
            171: (winner(18), winner(19)),
            172: (winner(20), winner(21)),
            181: (winner(171), winner(172)),
            182: (loser(171), loser(172)),
        }
    )
    specs[19] = {30 + i: (slot(1 + i * 2), slot(2 + i * 2)) for i in range(4)}
    specs[19].update(
        {
            141: (winner(30), winner(31)),
            142: (winner(32), winner(33)),
            151: (winner(141), winner(142)),
        }
    )
    specs[20] = {i: (None, None) for i in range(9, 14)}
    specs[20].update(
        {
            22: (display("SyntheticPlayer071"), winner(9)),
            23: (winner(10), winner(11)),
            24: (display("SyntheticPlayer077"), winner(12)),
            25: (winner(13), display("SyntheticPlayer057")),
            173: (winner(22), winner(23)),
            174: (winner(24), winner(25)),
            183: (winner(173), winner(174)),
            184: (loser(173), loser(174)),
        }
    )
    return specs


def compact(value):
    return "".join(value.split())


def geometry(page):
    """Preserve exact drawing primitives, including dotted bronze paths."""
    result = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "re":
                result.append({"type": "rect", "bbox": list(item[1])})
            elif item[0] == "l":
                result.append({"type": "line", "points": [list(item[1]), list(item[2])]})
            elif item[0] == "c":
                result.append({"type": "curve", "points": [list(p) for p in item[1:]]})
            elif item[0] == "qu":
                result.append({"type": "quad", "points": [list(p) for p in item[1]]})
    return result


def number_evidence(page, code):
    words = [w for w in page.get_text("words") if w[4] == str(code)]
    if len(words) != 1:
        raise ValueError(f"Expected one printed graph code {code}")
    return {"page": page.number + 1, "bbox": list(words[0][:4]), "text": str(code)}


def pool_edge_evidence(page, pool, matches):
    """Resolve printed display boxes to polygon vertices, then verify each edge.

    Text matching is confined to this source page/pool; it never establishes a
    tournament participant identity. Stroke rectangles contribute four sides.
    """
    segments = []
    for drawing in page.get_drawings():
        if drawing["type"] != "s":
            continue
        for item in drawing["items"]:
            if item[0] == "l":
                segments.append([list(item[1]), list(item[2])])
            elif item[0] == "re":
                r = item[1]
                corners = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]]
                segments.extend([[corners[i], corners[(i + 1) % 4]] for i in range(4)])
    if page.number in {0, 16}:
        # The five-player diagrams include a separate small score table; its
        # strokes are not entrant vertices or round-robin edges.
        segments = [s for s in segments if math.dist(*s) > 200]
    words = page.get_text("words")
    vertices = {}
    name_evidence = {}

    def distance(point, box):
        return math.hypot(
            max(box[0] - point[0], 0, point[0] - box[2]),
            max(box[1] - point[1], 0, point[1] - box[3]),
        )

    for entry in pool["entries"]:
        target = compact(entry["label"])
        boxes = []
        for start in range(len(words)):
            value = ""
            for end in range(start, min(start + 8, len(words))):
                value += compact(words[end][4])
                if value == target:
                    group = words[start : end + 1]
                    boxes.append(
                        [
                            min(w[0] for w in group),
                            min(w[1] for w in group),
                            max(w[2] for w in group),
                            max(w[3] for w in group),
                        ]
                    )
                    break
                if not target.startswith(value):
                    break
        if not boxes:
            raise ValueError(f"No printed pool name box: {entry['label']}")
        best = min(
            (
                (distance(point, box), point, box)
                for segment in segments
                for point in segment
                for box in boxes
            ),
            key=lambda item: item[0],
        )
        if best[0] > 70:
            raise ValueError(f"Pool name detached from vertex: {entry['label']}")
        vertices[entry["entry_id"]] = best[1]
        name_evidence[entry["entry_id"]] = {
            "page": page.number + 1,
            "bbox": best[2],
            "text": entry["label"],
            "vertex": best[1],
        }
    result = []
    for match in matches:
        points = [vertices[side["entry_id"]] for side in match["sides"]]
        edges = [
            s
            for s in segments
            if any(
                math.dist(s[0], points[i]) < 6 and math.dist(s[1], points[1 - i]) < 6
                for i in range(2)
            )
        ]
        if not edges:
            raise ValueError(
                f"Pool printed edge missing: {match['code']} {match['schedule_displays']} {points}"
            )
        edge = edges[0]
        box = match["number_evidence"]["bbox"]
        center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        vector = [edge[1][i] - edge[0][i] for i in range(2)]
        fraction = max(
            0,
            min(
                1,
                sum((center[i] - edge[0][i]) * vector[i] for i in range(2))
                / sum(v * v for v in vector),
            ),
        )
        code_distance = math.dist(center, [edge[0][i] + fraction * vector[i] for i in range(2)])
        if code_distance > 30:
            raise ValueError(f"Printed pool code detached from declared edge: {match['code']}")
        result.append(
            {
                "match_code": match["code"],
                "line_points": edge,
                "code_distance_points": code_distance,
                "side_evidence": [name_evidence[s["entry_id"]] for s in match["sides"]],
            }
        )
    return result


def match_bar(page, ref):
    """Match labels sit directly below their solid/dotted horizontal join."""
    x = (ref["bbox"][0] + ref["bbox"][2]) / 2
    y = ref["bbox"][1]
    rects = [p["bbox"] for p in geometry(page) if p["type"] == "rect"]
    near = [
        r
        for r in rects
        if r[2] - r[0] > 20
        and r[3] - r[1] < 8
        and r[0] - 20 <= x <= r[2] + 20
        and 0 <= y - r[3] < 15
    ]
    if near:
        return min(near, key=lambda r: abs(y - r[3]))
    dots = [r for r in rects if r[2] - r[0] < 4 and r[3] - r[1] < 4 and 0 <= y - r[3] < 15]
    if dots:
        return [
            min(r[0] for r in dots),
            min(r[1] for r in dots),
            max(r[2] for r in dots),
            max(r[3] for r in dots),
        ]
    raise ValueError(f"No horizontal printed connector for p{page.number + 1} {ref['text']}")


def validate_topology(graph):
    """Check every dependency against the PDF's horizontal joins and stems.

    This rejects a swapped branch even when all match numbers remain present.
    Bronze joins use dotted bars and the same two semifinal stems; the schedule
    independently labels these matches 季殿, rather than inferring loser semantics
    from a dashed path alone.
    """
    if graph["stage_type"] != "elimination":
        return []
    matches = {m["code"]: m for m in graph["matches"]}
    evidence = []
    rects = [p["bbox"] for p in graph["drawing_primitives"] if p["type"] == "rect"]
    for match in matches.values():
        parent = match["connector_bbox"]
        branch_x = []
        for side in match["sides"]:
            if side["type"] not in {"match_winner", "match_loser"}:
                continue
            child = matches[side["match_code"]]
            bar = child["connector_bbox"]
            x = (bar[0] + bar[2]) / 2
            low = parent[1]
            high = bar[3]
            if not parent[0] - 8 <= x <= parent[2] + 8 or high <= low:
                raise ValueError(f"Nonconnecting branch {child['code']} → {match['code']}")
            stems = sorted(
                [
                    r
                    for r in rects
                    if r[2] - r[0] < 8
                    and r[3] - r[1] > 10
                    and r[0] - 6 <= x <= r[2] + 6
                    and r[3] >= low - 6
                    and r[1] <= high + 6
                ],
                key=lambda r: r[1],
            )
            reached = low
            for stem in stems:
                if stem[1] <= reached + 6:
                    reached = max(reached, stem[3])
            if reached < high - 6:
                raise ValueError(f"No continuous printed stem {child['code']} → {match['code']}")
            if side["type"] == "match_loser" and match["schedule_stage_label"] != "季殿":
                raise ValueError(f"Unverified bronze semantics {match['code']}")
            evidence.append(
                {
                    "from_code": child["code"],
                    "to_code": match["code"],
                    "outcome": "loser" if side["type"] == "match_loser" else "winner",
                    "parent_join_bbox": parent,
                    "child_join_bbox": bar,
                    "stem_bboxes": stems,
                    "semantics_source": "printed_graph_and_schedule_stage",
                }
            )
            branch_x.append(x)
        if branch_x != sorted(branch_x):
            raise ValueError(f"Printed left/right branch order differs: {match['code']}")
    return evidence


def build_catalog(project: Path = PROJECT, source: Path | None = None):
    project = Path(project)
    source = source or next(project.parent.glob("*20261023.pdf"))
    published = json.loads(
        (project / "output/full-event-demo/publication-model.json").read_text("utf-8-sig")
    )
    rows = {r["code"]: r for r in published["matches"]}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    graphs_meta = json.loads(
        (project / "output/full-event-demo/graph-metadata.json").read_text("utf-8-sig")
    )
    metadata = {g["source_page"]: g for g in graphs_meta.values()}
    brackets = bracket_specs()
    graphs = []
    verified = 0
    with pymupdf.open(source) as pdf:
        for page_number in range(1, 26):
            page = pdf[page_number - 1]
            meta = metadata[page_number]
            text = page.get_text()
            if meta["source_sha256"] != digest:
                raise ValueError("Graph source fingerprint differs")
            graph = {
                "id": f"source:graph:{page_number}",
                "source_page": page_number,
                "division": meta["division"],
                "stage_label": meta["stage"],
                "stage_type": "round_robin" if page_number in POOL_SPECS else "elimination",
                "source_sha256": digest,
                "page_bbox": list(page.rect),
                "source_text": text,
                "drawing_primitives": geometry(page),
                "pools": [],
                "matches": [],
                "unlabelled_edges": [],
                "uncertainties": [],
                "editable_topology": True,
                "historical_sample_already_generated": page_number in {1, 8, 9, 18, 19},
            }

            def append(code, sides, pool_id=None, *, graph=graph, page=page, text=text):
                nonlocal verified
                code = str(code)
                row = rows[code]
                ref = row["source_ref"]
                clip = pdf[ref["page"] - 1].get_text(clip=pymupdf.Rect(ref["bbox"]))
                if ref["sha256"] != digest or compact(clip) != compact(ref["text"]):
                    raise ValueError(f"Schedule evidence differs: {code}")
                if compact(row["division"]) not in compact(graph["division"]):
                    raise ValueError(f"Graph/table division differs: {code}")
                number = number_evidence(page, code)
                resolved = []
                for i, side in enumerate(sides):
                    side = side or display(row["side_a" if i == 0 else "side_b"])
                    if side["type"] == "source_display":
                        if not side["label"] or compact(side["label"]) not in compact(text):
                            raise ValueError(f"Graph display missing {code}: {side}")
                        side = {
                            **side,
                            "entry_id": f"{pool_id or graph['id']}:display:{compact(side['label'])}",
                            "identity_status": "source_display_only",
                        }
                    elif side["type"] == "draw_slot":
                        side = {**side, "draw_run_id": graph["id"] + ":unassigned-leaves"}
                    resolved.append(side)
                item = {
                    "code": code,
                    "id": "source:match:" + code,
                    "sides": resolved,
                    "pool_id": pool_id,
                    "number_evidence": number,
                    "schedule_evidence": ref,
                    "schedule_displays": [row["side_a"], row["side_b"]],
                    "court": None,
                    "schedule_stage_label": row["stage"],
                    "kind": "bronze"
                    if resolved[0]["type"] == "match_loser"
                    else "final"
                    if row["stage"] == "冠亞"
                    else "match",
                }
                if graph["stage_type"] == "elimination":
                    item["connector_bbox"] = match_bar(page, number)
                graph["matches"].append(item)
                verified += 1

            if page_number in POOL_SPECS:
                for index, (label, count, advance, codes) in enumerate(POOL_SPECS[page_number]):
                    pool_id = f"{graph['id']}:pool:{index + 1}"
                    names = list(
                        dict.fromkeys(rows[str(c)][s] for c in codes for s in ("side_a", "side_b"))
                    )
                    if len(names) != count:
                        raise ValueError(f"Pool entries differ: {pool_id}")
                    graph["pools"].append(
                        {
                            "id": pool_id,
                            "label": label,
                            "source_position": index + 1,
                            "entry_count": count,
                            "advancement_count": advance,
                            "advancement_evidence_text": f"{count}取{advance}進決賽"
                            if advance
                            else None,
                            "entries": [
                                {
                                    "entry_id": f"{pool_id}:display:{compact(n)}",
                                    "label": n,
                                    "identity_status": "source_display_only",
                                }
                                for n in names
                            ],
                            "match_codes": list(map(str, codes)),
                        }
                    )
                    if advance and f"{count}取{advance}進決賽" not in compact(text).replace(
                        "２", "2"
                    ):
                        raise ValueError(f"Advancement rule differs: {pool_id}")
                    for c in codes:
                        append(c, [display(rows[str(c)][s]) for s in ("side_a", "side_b")], pool_id)
                    graph.setdefault("pool_connection_evidence", []).extend(
                        pool_edge_evidence(
                            page,
                            graph["pools"][-1],
                            [m for m in graph["matches"] if m["pool_id"] == pool_id],
                        )
                    )
            else:
                for code, sides in brackets[page_number].items():
                    append(code, sides)
            if page_number == 1:
                right_edge = [
                    p
                    for p in graph["drawing_primitives"]
                    if p["type"] == "line"
                    and all(point[0] > 490 for point in p["points"])
                    and abs(p["points"][0][1] - p["points"][1][1]) > 200
                ]
                if len(right_edge) != 1:
                    raise ValueError("Unlabelled B right pentagon edge differs")
                graph["unlabelled_edges"] = [
                    {
                        "id": "source:graph:1:unlabelled:1",
                        "type": "source_only_unlabelled_pool_edge",
                        "pool_id": graph["pools"][0]["id"],
                        "sides": [display("SyntheticAlias007"), display("SyntheticPlayer002")],
                        "code": None,
                        "schedule_match_id": None,
                        "source_page": 1,
                        "line_evidence": right_edge[0],
                        "evidence": "Printed right lower pentagon edge; no printed code on graph or matching schedule row. Never assign a synthetic official match number.",
                    }
                ]
                for side in graph["unlabelled_edges"][0]["sides"]:
                    side.update(
                        entry_id=f"{graph['pools'][0]['id']}:display:{compact(side['label'])}",
                        identity_status="source_display_only",
                    )
                graph["uncertainties"].append(
                    "男子B完整五人循環10邊，僅9邊印場號；SyntheticAlias007—SyntheticPlayer002右下邊未編號。195確已印在圖p1及時間表p34，為SyntheticPlayer003—SyntheticPlayer002。"
                )
            if page_number in {3, 9}:
                graph["draw_constraints"] = {
                    "first_place_allowed_slots": ["1", "3", "6"],
                    "source_text": "小組第一名抽：①  ③  ⑥",
                    "actual_assignments_known": False,
                }
            if page_number in {3, 5, 7, 9, 11, 13, 15}:
                graph["uncertainties"].append(
                    "未印之初始葉保留抽位；已印A/B名次或抽籤限制照錄，實際抽籤結果未提供。"
                )
            if page_number in {19, 21}:
                graph["uncertainties"].append(
                    "鼓勵獎內部勝者連線已知；初始葉來源及是否由特定主賽敗者進入未印，不推定敗者對應。"
                )
            if page_number in {1, 17}:
                if compact("勝:2分 敗:1分 棄:0分") not in compact(text):
                    raise ValueError("Printed round-robin scoring differs")
                graph["scoring"] = {
                    "win": 2,
                    "loss": 1,
                    "forfeit": 0,
                    "source_text": "勝:2分　敗:1分　棄:0分",
                }
            if graph["pools"] and graph["pools"][0]["advancement_count"]:
                graph["advancement_to_next_graph"] = {
                    "target_graph_id": f"source:graph:{page_number + 1}",
                    "qualifiers": [
                        {"pool_id": p["id"], "ranks": list(range(1, p["advancement_count"] + 1))}
                        for p in graph["pools"]
                    ],
                    "slot_assignment_status": "only_explicit_printed_ranks_or_draw_constraints_known",
                }
            graph["typed_match_count"] = len(graph["matches"])
            graph["printed_match_codes"] = sorted((m["code"] for m in graph["matches"]), key=int)
            fresh_codes = sorted(
                (w[4] for w in page.get_text("words") if w[4].isascii() and w[4].isdigit()), key=int
            )
            if fresh_codes != graph["printed_match_codes"]:
                raise ValueError(f"Graph number coverage differs p{page_number}: {fresh_codes}")
            graph["connection_evidence"] = validate_topology(graph)
            graphs.append(graph)
    all_codes = [m["code"] for g in graphs for m in g["matches"]]
    numbered = {c for c in rows if c.isdigit()}
    if len(all_codes) != len(set(all_codes)) or set(all_codes) != numbered:
        raise ValueError("Catalogue/table exact code coverage differs")
    division_readiness = []
    for division in dict.fromkeys(g["division"] for g in graphs):
        stages = [g for g in graphs if g["division"] == division]
        division_readiness.append(
            {
                "division": division,
                "source_pages": [g["source_page"] for g in stages],
                "stages": [
                    {
                        "graph_id": g["id"],
                        "type": g["stage_type"],
                        "label": g["stage_label"],
                        "match_codes": g["printed_match_codes"],
                    }
                    for g in stages
                ],
                "editable_topology": True,
                "historical_sample_missing_pages": [
                    g["source_page"] for g in stages if not g["historical_sample_already_generated"]
                ],
                "uncertainties": [u for g in stages for u in g["uncertainties"]],
            }
        )
    return {
        "schema_version": "1.0.0",
        "status": "historical_source_evidence_not_official_draw",
        "source_file": source.name,
        "source_sha256": digest,
        "graphs": graphs,
        "division_readiness": division_readiness,
        "verification": {
            "fresh_pdf_pages": 25,
            "schedule_row_clips": verified,
            "exact_numbered_schedule_code_coverage": True,
            "numbered_codes": sorted(numbered, key=int),
            "schedule_only_unnumbered_rows": [r for r in rows.values() if not r["code"].isdigit()],
        },
        "readiness": {
            "editable_graphs": 25,
            "typed_matches": len(all_codes),
            "source_only_unlabelled_edges": 1,
            "historical_samples_already_generated_pages": [1, 8, 9, 18, 19],
            "remaining_historical_generation_pages": [
                p for p in range(1, 26) if p not in {1, 8, 9, 18, 19}
            ],
            "participant_identities_approved": False,
            "actual_draw_assignments_known": False,
            "historical_courts_known": False,
        },
    }


def with_generation_evidence(catalog, page_mapping, page_count):
    """Replace the initial sample baseline with actual validated output destinations."""
    expected = {str(graph["source_page"]) for graph in catalog["graphs"]}
    if set(page_mapping) != expected:
        raise ValueError("Generated source page mapping does not cover the complete catalog")
    pages = [number for values in page_mapping.values() for number in values]
    if (
        any(not values for values in page_mapping.values())
        or any(type(number) is not int or not 1 <= number <= page_count for number in pages)
        or len(pages) != len(set(pages))
    ):
        raise ValueError("Generated source page mapping is ambiguous or outside the PDF")
    delivered = deepcopy(catalog)
    for graph in delivered["graphs"]:
        graph["historical_sample_already_generated"] = True
        graph["generated_pdf_pages"] = list(page_mapping[str(graph["source_page"])])
    for division in delivered["division_readiness"]:
        division["historical_sample_missing_pages"] = []
    delivered["readiness"].update(
        historical_samples_already_generated_pages=sorted(map(int, expected)),
        remaining_historical_generation_pages=[],
        status_scope="validated_delivered_pdf",
        generated_pdf_page_count=page_count,
    )
    return delivered


def render_html(catalog, *, json_filename="catalog.json"):
    body = "<h1>原稿25圖結構與重建準備度</h1><p>來源證據草稿；選手顯示文字不代表身份核准。所有圖內連線已結構化，可用typed資料產生可編輯圖；未印抽位、鼓勵獎入口與球場維持未知。</p>"
    body += (
        '<p><a href="'
        + escape(json_filename, quote=True)
        + '">完整JSON與PDF座標證據</a></p><pre>'
        + escape(json.dumps(catalog["readiness"], ensure_ascii=False, indent=2))
        + "</pre>"
    )
    body += "<table><tr><th>頁/組別</th><th>賽制與晉級</th><th>場號</th><th>樣張／待完成</th></tr>"
    for g in catalog["graphs"]:
        rules = "；".join(
            (p["label"] or "位置" + str(p["source_position"]))
            + f"：{p['entry_count']}人／取{p['advancement_count'] or '循環排名'}"
            for p in g["pools"]
        )
        body += f"<tr><td>p{g['source_page']} {escape(g['division'])} {escape(g['stage_label'])}</td><td>{escape(rules or '淘汰；圖內勝者／敗者來源已錄')}</td><td>{', '.join(g['printed_match_codes'])}</td><td>{'已交樣張' if g['historical_sample_already_generated'] else '可從typed資料重建，尚未交圖'}</td></tr>"
    body += "</table>"
    for g in catalog["graphs"]:
        body += f"<details><summary>p{g['source_page']} {escape(g['division'])}：關係與真正未知項</summary>"
        body += "".join("<p>" + escape(u) + "</p>" for u in g["uncertainties"])
        body += (
            "<pre>"
            + escape(
                json.dumps(
                    {
                        "pools": g["pools"],
                        "matches": [
                            {k: m[k] for k in ("code", "sides", "number_evidence", "kind")}
                            for m in g["matches"]
                        ],
                        "unlabelled_edges": g["unlabelled_edges"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            + "</pre></details>"
        )
    return (
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>歷史圖結構目錄</title><style>body{font:16px sans-serif;max-width:1400px;margin:30px auto;line-height:1.6}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:9px}pre{white-space:pre-wrap}details{margin:15px 0;background:#f2f5f4;padding:12px}</style>'
        + body
        + "</html>"
    )


def main():
    catalog = build_catalog()
    output = PROJECT / "output/historical-graph-catalog"
    output.mkdir(parents=True, exist_ok=True)
    (output / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (output / "index.html").write_text(render_html(catalog), "utf-8")
    print(json.dumps(catalog["readiness"], ensure_ascii=False))


if __name__ == "__main__":
    main()
