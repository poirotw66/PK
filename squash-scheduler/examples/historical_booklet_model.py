"""Project the verified 25 source diagrams and 232 schedule rows to a draft model.

build_model never writes files, creates participant identities, assigns a draw,
or turns the single unnumbered source edge into an official match.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from runpy import run_path

import pymupdf


def build_model(project: Path, font_asset_id: str | None = None) -> dict:
    project = Path(project).resolve()
    helpers = Path(__file__).resolve().parent
    # Reuse the existing fresh-PDF notice/QR/sponsor checks rather than copying
    # historical sponsor strings into a newly configured event workflow.
    model = run_path(str(helpers / "historical_booklet_samples.py"))["build_model"](
        project, font_asset_id
    )
    catalog = run_path(str(helpers / "historical_graph_catalog.py"))["build_catalog"](project)
    published = json.loads(
        (project / "output/full-event-demo/publication-model.json").read_text("utf-8-sig")
    )
    originals = {row["code"]: row for row in published["matches"]}
    source = project.parent / catalog["source_file"]
    rows = []
    stages = []
    pools = []
    divisions = []
    entries = {}
    edges = []
    deferrals = []
    division_ids = {}
    original_order = {row["code"]: index for index, row in enumerate(published["matches"])}

    for graph in catalog["graphs"]:
        name = graph["division"]
        if name not in division_ids:
            division_ids[name] = f"source:division:{len(division_ids) + 1}"
            divisions.append({"id": division_ids[name], "name": name})
        stage_id = graph["id"]
        stages.append(
            {
                "id": stage_id,
                "division_id": division_ids[name],
                "type": graph["stage_type"],
                "order": graph["source_page"] - 1,
                "label": graph["stage_label"],
                "source_page": graph["source_page"],
                "topology_status": "source_geometry_verified",
                "evidence": True,
                "source_sha256": catalog["source_sha256"],
            }
        )
        for pool in graph["pools"]:
            pools.append(
                {**deepcopy(pool), "stage_id": stage_id, "source_page": graph["source_page"]}
            )
            for entry in pool["entries"]:
                entries[entry["entry_id"]] = {
                    **entry,
                    "source_page": graph["source_page"],
                    "identity_status": "source_display_only",
                }

    def convert(side, graph):
        kind = side["type"]
        if kind == "source_display":
            identifier = side["entry_id"]
            entries.setdefault(
                identifier,
                {
                    "entry_id": identifier,
                    "label": side["label"],
                    "source_page": graph["source_page"],
                    "identity_status": "source_display_only",
                },
            )
            return side["label"], {"type": "entry", "entry_id": identifier}
        if kind in {"match_winner", "match_loser"}:
            return side["match_code"] + (" 勝者" if kind == "match_winner" else " 敗者"), {
                "type": kind,
                "match_id": "source:match:" + side["match_code"],
            }
        if kind == "draw_slot":
            number = side["slot_id"]
            label = (
                chr(0x245F + int(number)) if graph["source_page"] in {3, 9} else "待定籤位" + number
            )
            return label, {
                "type": "draw_slot",
                "slot_id": number,
                "draw_run_id": side["draw_run_id"],
            }
        if kind == "pool_rank":
            candidates = [
                p
                for p in pools
                if p["label"] == side["pool_label"]
                and next(s for s in stages if s["id"] == p["stage_id"])["division_id"]
                == division_ids[graph["division"]]
                and p["source_page"] < graph["source_page"]
            ]
            if len(candidates) != 1:
                raise ValueError(f"Ambiguous printed pool rank {graph['source_page']} {side}")
            return side["pool_label"] + str(side["rank"]), {
                "type": "pool_rank",
                "pool_id": candidates[0]["id"],
                "rank": side["rank"],
            }
        raise ValueError(f"Unsupported historical source side: {kind}")

    def make_row(code, stage, division, side_a, side_b, position, source_page):
        row = originals[code]
        return {
            "match_id": "source:match:" + code,
            "code": code,
            "division_id": stage["division_id"],
            "division": division,
            "stage_id": stage["id"],
            "stage": stage["label"],
            "kind": "季殿／名次賽" if side_a[1]["type"] == "match_loser" else "原稿逐場證據",
            "side_a": side_a[0],
            "side_a_ref": side_a[1],
            "side_b": side_b[0],
            "side_b_ref": side_b[1],
            "source_schedule_sides": [row["side_a"], row["side_b"]],
            "source_schedule_order": original_order[code],
            "date": row["date"] or "原稿未載",
            "time": row["time"] or "原稿未載",
            "court": "原稿未載",
            "court_order": 0,
            "position": position,
            "round": None,
            "sources": [side[1]["match_id"] for side in (side_a, side_b) if "match_id" in side[1]],
            "source_ref": deepcopy(row["source_ref"]),
            "published_source": deepcopy(row),
            "source_page": source_page,
            "evidence": True,
            "evidence_status": "draft_source_evidence",
            "participant_identity_established": False,
        }

    for graph in catalog["graphs"]:
        stage = next(s for s in stages if s["id"] == graph["id"])
        for position, match in enumerate(graph["matches"]):
            a, b = [convert(side, graph) for side in match["sides"]]
            rows.append(
                make_row(
                    match["code"], stage, graph["division"], a, b, position, graph["source_page"]
                )
            )
        for edge in graph["unlabelled_edges"]:
            a, b = [convert(side, graph) for side in edge["sides"]]
            edges.append(
                {
                    "id": edge["id"],
                    "stage_id": stage["id"],
                    "pool_id": edge["pool_id"],
                    "side_a": a[0],
                    "side_a_ref": a[1],
                    "side_b": b[0],
                    "side_b_ref": b[1],
                    "source_ref": {
                        "kind": "published_pdf_unlabelled_graph_edge",
                        "sha256": catalog["source_sha256"],
                        "file_name": catalog["source_file"],
                        "page": graph["source_page"],
                        "line_evidence": deepcopy(edge["line_evidence"]),
                    },
                    "evidence": True,
                    "official_match_created": False,
                }
            )
        slots = [
            side
            for match in graph["matches"]
            for side in match["sides"]
            if side["type"] == "draw_slot"
        ]
        if slots:
            deferrals.append(
                {
                    "id": slots[0]["draw_run_id"],
                    "status": "未執行或核准抽籤；來源葉位置證據",
                    "procedure": {
                        "description": "原稿小組第一名抽①③⑥；未指派實際入籤選手。"
                        if graph["source_page"] in {3, 9}
                        else "待定籤位僅標示原圖葉位置；不推定入籤選手或主賽敗者。"
                    },
                }
            )

    exhibition_id = "source:division:exhibition"
    divisions.append({"id": exhibition_id, "name": "表演賽"})
    appendix = {
        "id": "source:appendix:exhibition",
        "division_id": exhibition_id,
        "type": "elimination",
        "order": 25,
        "label": "表演賽（原稿另列）",
        "source_page": None,
        "source_schedule_pages": [31, 35],
        "independent_matches": True,
        "evidence": True,
    }
    stages.append(appendix)
    with pymupdf.open(source) as pdf:
        for position, row in enumerate(r for r in published["matches"] if not r["code"].isdigit()):
            ref = row["source_ref"]
            clip = pdf[ref["page"] - 1].get_text(clip=pymupdf.Rect(ref["bbox"]))
            if "".join(clip.split()) != "".join(ref["text"].split()):
                raise ValueError("Exhibition source schedule clip differs")
            sides = []
            for key in ("side_a", "side_b"):
                identifier = f"source:exhibition:{row['code']}:{key}"
                entries[identifier] = {
                    "entry_id": identifier,
                    "label": row[key],
                    "source_page": ref["page"],
                    "identity_status": "source_display_only",
                }
                sides.append((row[key], {"type": "entry", "entry_id": identifier}))
            item = make_row(row["code"], appendix, "表演賽", *sides, position, None)
            item["kind"] = "原稿另列表演賽"
            item["source_graph_exists"] = False
            rows.append(item)
        notes = [
            {
                "source_page": index + 1,
                "extracted_text": page.get_text(),
                "source_sha256": catalog["source_sha256"],
            }
            for index, page in enumerate(pdf)
        ]

    if {row["code"] for row in rows} != set(originals) or len(rows) != 232:
        raise ValueError("Historical projection must preserve all 232 source schedule rows")
    rows.sort(key=lambda row: row["source_schedule_order"])
    model.update(
        name="中正盃全組原稿重建來源草稿",
        public_name="中正盃・全組原稿重建草稿",
        revision_id="source-evidence-all-groups-no-approved-revision",
        evidence=True,
        matches=rows,
        stages=stages,
        pools=pools,
        divisions=divisions,
        source_entries=list(entries.values()),
        source_notes=notes,
        unlabelled_edges=edges,
        deferrals=deferrals,
        historical_graph_catalog=catalog,
    )
    model["event_dates"] = list(
        dict.fromkeys(row["date"] for row in rows if row["date"] != "原稿未載")
    )
    model["coverage_note"] = (
        "本包重建全部25張原稿對戰圖與232筆公布賽程，保留原表順序與左右欄。原稿未載的球場、實際抽位及鼓勵賽入籤來源仍待確認，不推定別名或參賽身分。男子B未編號圖線不計作正式場次。本包不含已核准名冊，完整Word報名名冊請另看四場地新編模擬版。"
    )
    model["publication"].update(
        layout_template_id="reference-v2",
        document_profile="order_book",
        historical_layout_verified=False,
    )
    model["rule_summaries"] = [
        {
            "title": "原稿已載積分",
            "lines": ["男子B組及高中女子組：勝2分、敗1分、棄0分；本項僅依原圖已印文字。"],
        },
        {
            "title": "來源草稿待確認事項",
            "lines": [
                "選手身份、實際抽位與正式發布尚未核准；原稿未載球場與時間不補排。",
                "鼓勵獎內部連線已按原稿重建；初始入籤來源未印，不指定主賽敗者。",
                "男子B原表含195等9場；SyntheticAlias007—SyntheticPlayer002原圖邊未編號，僅作來源圖線，不建立正式場次。",
                "完整競賽章程、局數、排名同分處理等原稿未提供事項仍待確認。",
            ],
        },
    ]
    model["metadata"].update(
        source_pages=list(range(1, 37)),
        source_graph_pages=list(range(1, 26)),
        source_schedule_rows=232,
        source_numbered_matches=230,
        historical_graph_catalog_schema=catalog["schema_version"],
        historical_projection="all_25_graphs_and_232_schedule_rows",
        source_schedule_sides_policy="preserve_original_columns_in_schedule_projection",
        participant_identities_approved=False,
        draw_assignments_approved=False,
        schedule_approved=False,
        encouragement_initial_slots_assigned=False,
        notice_sponsor_scope="historical_source_only",
        complete_regulations_provided=False,
        catalog_input_readiness_scope="initial_47_match_sample_baseline; delivered graph-catalog.json contains validated generation pages",
    )
    model["metadata"]["roster_scope"] = (
        "Original PDF reconstruction has no approved roster; the separate Word-source roster remains in the new-event demo and is not linked to graph display leaves."
    )
    model["content_hash"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in model.items() if k != "content_hash"},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return model
