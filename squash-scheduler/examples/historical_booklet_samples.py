"""Typed historical source evidence for review samples, never an official draw.

build_model(project: Path, font_asset_id: str | None = None) returns the document
view model without writing files or creating a tournament Snapshot. F-group and
school main/encouragement internal relations follow printed PDF lines. The eight
entrants to the encouragement bracket remain unknown and are never inferred from
main-draw losers. All entry IDs represent source labels, not approved identities.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
import zxingcpp
from PIL import Image


def school_printed_sides(originals: dict[str, dict]) -> dict:
    """PDF p18/19 graph lines; p33 labels distinguish final and bronze.

    Entry IDs are per printed initial match side. Name equality is never used to
    merge identities or assign the eight unknown encouragement draw positions.
    """
    sides = {}
    for code in map(str, range(1, 9)):
        row = originals[code]
        sides[code] = tuple(
            (row[side], {"type": "entry", "entry_id": f"source:school:{code}:{side}"})
            for side in ("side_a", "side_b")
        )
    for index, code in enumerate(("30", "31", "32", "33")):
        sides[code] = tuple(
            (
                f"籤位{index * 2 + side + 1}待核",
                {
                    "type": "draw_slot",
                    "slot_id": str(index * 2 + side + 1),
                    "draw_run_id": "source:school:encouragement-slots",
                },
            )
            for side in range(2)
        )
    for code, sources, loser in [
        ("18", ("1", "2"), False),
        ("19", ("3", "4"), False),
        ("20", ("5", "6"), False),
        ("21", ("7", "8"), False),
        ("171", ("18", "19"), False),
        ("172", ("20", "21"), False),
        ("181", ("171", "172"), False),
        ("182", ("171", "172"), True),
        ("141", ("30", "31"), False),
        ("142", ("32", "33"), False),
        ("151", ("141", "142"), False),
    ]:
        sides[code] = tuple(
            (
                source + (" 敗者" if loser else " 勝者"),
                {
                    "type": "match_loser" if loser else "match_winner",
                    "match_id": "source:match:" + source,
                },
            )
            for source in sources
        )
    return sides


def build_model(project: Path, font_asset_id: str | None = None) -> dict:
    project = Path(project).resolve()
    original = json.loads(
        (project / "output/full-event-demo/publication-model.json").read_text("utf-8-sig")
    )
    source = next(project.parent.glob("*20261023.pdf"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    originals = {row["code"]: row for row in original["matches"]}
    if any(row["source_ref"]["sha256"] != digest for row in originals.values()):
        raise ValueError("Historical sample source PDF fingerprint differs")
    pool_id, final_id, draw_id = "source:F:pool", "source:F:final", "source:F:printed-slots"
    stages, pools, rows, divisions, notes = [], [], [], [], []
    typed: dict[str, tuple[tuple[str, dict], tuple[str, dict]]] = {}
    with pymupdf.open(source) as pdf:
        # Verify every evidence row used by this example against fresh PDF text.
        def verify(code: str) -> dict:
            row = originals[code]
            ref = row["source_ref"]
            text = pdf[ref["page"] - 1].get_text(clip=pymupdf.Rect(ref["bbox"]))
            if "".join(text.split()) != "".join(ref["text"].split()):
                raise ValueError(f"Source clip differs for sample match {code}")
            return row

        for label, codes in zip(
            "ABC", (("44", "63", "82"), ("45", "64", "83"), ("46", "65", "84")), strict=True
        ):
            first, second = verify(codes[0]), verify(codes[1])
            # Clockwise page-8 triangle vertices; these IDs are evidence labels.
            names = (first["side_a"], second["side_b"], first["side_b"])
            entries = [
                {"entry_id": f"source:F:{label}:{i}", "label": name} for i, name in enumerate(names)
            ]
            refs = {e["label"]: {"type": "entry", "entry_id": e["entry_id"]} for e in entries}
            pools.append(
                {
                    "id": f"source:F:{label}",
                    "stage_id": pool_id,
                    "label": label,
                    "advancement_count": 2,
                    "entries": entries,
                    "source_page": 8,
                }
            )
            for code in codes:
                row = verify(code)
                typed[code] = (
                    (row["side_a"], refs[row["side_a"]]),
                    (row["side_b"], refs[row["side_b"]]),
                )

        def slot(number: int) -> tuple[str, dict]:
            return chr(0x245F + number), {
                "type": "draw_slot",
                "slot_id": str(number),
                "draw_run_id": draw_id,
            }

        def dependency(code: str, loser: bool = False) -> tuple[str, dict]:
            return code + (" 敗者" if loser else " 勝者"), {
                "type": "match_loser" if loser else "match_winner",
                "match_id": "source:match:" + code,
            }

        typed.update(
            {
                "185": (slot(2), slot(3)),
                "186": (slot(4), slot(5)),
                "204": (slot(1), dependency("185")),
                "205": (dependency("186"), slot(6)),
                "220": (dependency("204"), dependency("205")),
                "221": (dependency("204", True), dependency("205", True)),
            }
        )
        divisions.append({"id": "source:F", "name": "男子F組"})
        stages.extend(
            [
                {
                    "id": pool_id,
                    "division_id": "source:F",
                    "type": "round_robin",
                    "order": 0,
                    "label": "預賽（三池各取二）",
                    "source_page": 8,
                },
                {
                    "id": final_id,
                    "division_id": "source:F",
                    "type": "elimination",
                    "order": 1,
                    "label": "決賽（原稿籤位）",
                    "source_page": 9,
                },
            ]
        )

        def append(
            code: str,
            division_id: str,
            division: str,
            stage_id: str,
            stage: str,
            position: int = 0,
            source_page: int | None = None,
        ) -> None:
            row = verify(code)
            a, b = typed.get(
                code,
                (
                    (
                        row.get("side_a") or "來源待確認",
                        {
                            "type": "draw_slot",
                            "slot_id": code + ":a",
                            "draw_run_id": "source:unresolved",
                        },
                    ),
                    (
                        row.get("side_b") or "來源待確認",
                        {
                            "type": "draw_slot",
                            "slot_id": code + ":b",
                            "draw_run_id": "source:unresolved",
                        },
                    ),
                ),
            )
            rows.append(
                {
                    "match_id": "source:match:" + code,
                    "code": code,
                    "division_id": division_id,
                    "division": division,
                    "stage_id": stage_id,
                    "stage": stage,
                    "kind": "季殿／名次賽" if code in {"221", "182"} else "原稿逐場證據",
                    "side_a": a[0],
                    "side_a_ref": a[1],
                    "side_b": b[0],
                    "side_b_ref": b[1],
                    "date": row["date"],
                    "time": row["time"] or "原稿未載",
                    "court": "原稿未載",
                    "court_order": 0,
                    "position": position,
                    "round": None,
                    "sources": [ref["match_id"] for ref in (a[1], b[1]) if "match_id" in ref],
                    "source_ref": row["source_ref"],
                    "published_source": row,
                    "source_page": source_page,
                    "evidence_status": "draft_source_evidence",
                    "participant_identity_established": False,
                }
            )

        for code in typed:
            append(
                code,
                "source:F",
                "男子F組",
                pool_id if int(code) < 100 else final_id,
                "預賽" if int(code) < 100 else "決賽",
                source_page=8 if int(code) < 100 else 9,
            )

        # B: keep the 9 published rows, including 195. The SyntheticAlias007/Wu edge has no code.
        b_rows = [r for r in original["matches"] if r["division"] == "男子B"]
        b_entries = [
            {"entry_id": f"source:B:{i}", "label": name}
            for i, name in enumerate(
                dict.fromkeys(r[side] for r in b_rows for side in ("side_a", "side_b"))
            )
        ]
        b_refs = {e["label"]: {"type": "entry", "entry_id": e["entry_id"]} for e in b_entries}
        divisions.insert(0, {"id": "source:B", "name": "男子B組"})
        stages.insert(
            0,
            {
                "id": "source:B:roundrobin",
                "division_id": "source:B",
                "type": "round_robin",
                "order": 0,
                "label": "循環賽（原表9列）",
                "source_page": 1,
            },
        )
        pools.append(
            {
                "id": "source:B:pool",
                "stage_id": "source:B:roundrobin",
                "label": "",
                "advancement_count": None,
                "entries": b_entries,
                "source_page": 1,
            }
        )
        for row in b_rows:
            code = row["code"]
            typed[code] = (
                (row["side_a"], b_refs[row["side_a"]]),
                (row["side_b"], b_refs[row["side_b"]]),
            )
            append(code, "source:B", "男子B組", "source:B:roundrobin", "循環賽", source_page=1)

        # Internal graph relations are explicitly printed. Only the initial eight
        # encouragement entrants and their connection to the main draw are unknown.
        typed.update(school_printed_sides(originals))
        divisions.append({"id": "source:school-boys", "name": "國中男子組"})
        for page_number, label, codes in [
            (
                18,
                "一至四名（原稿連線）",
                [
                    "1",
                    "2",
                    "3",
                    "4",
                    "5",
                    "6",
                    "7",
                    "8",
                    "18",
                    "19",
                    "20",
                    "21",
                    "171",
                    "172",
                    "181",
                    "182",
                ],
            ),
            (19, "鼓勵獎（來源待核）", ["30", "31", "32", "33", "141", "142", "151"]),
        ]:
            printed_tokens = pdf[page_number - 1].get_text().split()
            if not all(code in printed_tokens for code in codes):
                raise ValueError(f"Printed school match codes differ on page {page_number}")
            stage_id = "source:school:" + codes[0]
            stages.append(
                {
                    "id": stage_id,
                    "division_id": "source:school-boys",
                    "type": "elimination",
                    "order": len(stages),
                    "label": label,
                    "source_page": page_number,
                    "topology_status": "printed_internal_relations",
                    "initial_entrants_status": "source_display_only"
                    if page_number == 18
                    else "unassigned_draw_slots",
                }
            )
            for position, code in enumerate(codes):
                append(
                    code,
                    "source:school-boys",
                    "國中男子組",
                    stage_id,
                    label,
                    position,
                    source_page=page_number,
                )
        # Decode the printed historical notice locally; never fetch its URL or
        # carry these sponsor terms into a newly configured event.
        notice_page = pdf[35]
        notice_text = notice_page.get_text()
        notice_lines = [line.strip() for line in notice_text.splitlines() if line.strip()]
        qr_label = "中正盃壁球錦標賽即時場次通知"
        if not notice_lines or notice_lines[0] != qr_label:
            raise ValueError("Historical notice title differs on page 36")
        if "感謝SyntheticAlias008設計贊助" not in "".join(notice_text.split()):
            raise ValueError("Historical design sponsorship differs on page 36")
        pixmap = notice_page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
        notice_image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        decoded = {
            result.text
            for result in zxingcpp.read_barcodes(notice_image)
            if result.format == zxingcpp.BarcodeFormat.QRCode
        }
        if len(decoded) != 1:
            raise ValueError("Historical page 36 must contain one unambiguous QR URL")
        qr_url = decoded.pop()
        parsed_url = urlsplit(qr_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname:
            raise ValueError("Historical notice QR requires an HTTPS URL")
        for page_number in (1, 8, 9, 18, 19, 36):
            notes.append(
                {
                    "source_page": page_number,
                    "extracted_text": pdf[page_number - 1].get_text(),
                    "source_sha256": digest,
                }
            )

    model = {
        "schema_version": "1.0.0",
        "name": "中正盃歷史資料代表頁核對草案",
        "public_name": "中正盃・原稿證據樣張",
        "venue": "原稿未載",
        "timezone": "Asia/Taipei",
        "draft": True,
        "revision_id": "source-evidence-samples-no-approved-revision",
        "publication": {
            "layout_template_id": "reference-v1",
            "paper_size": "A4",
            "orientation": "landscape",
            "font_asset_id": font_asset_id,
            "historical_layout_verified": False,
            "qr_mode": "enabled",
            "qr_url": qr_url,
            "qr_label": qr_label,
            "sponsor_mode": "provided",
            "sponsors": [{"display_name": "感謝 SyntheticAlias008", "text": "設計贊助"}],
        },
        "matches": rows,
        "stages": stages,
        "pools": pools,
        "unlabelled_edges": [
            {
                "stage_id": "source:B:roundrobin",
                "side_a_ref": b_refs["SyntheticAlias007"],
                "side_b_ref": b_refs["SyntheticPlayer002"],
                "source_ref": {
                    "kind": "published_pdf_unlabelled_graph_edge",
                    "sha256": digest,
                    "file_name": source.name,
                    "page": 1,
                },
            }
        ],
        "divisions": divisions,
        "participants": [],
        "roster": [],
        "roster_candidates": [],
        "courts": [],
        "issues": [],
        "issue_history": [],
        "changes": [],
        "deferrals": [
            {
                "id": draw_id,
                "status": "原稿籤位文字證據；未執行或核准抽籤",
                "procedure": {
                    "description": "原稿小組第一名抽①③⑥；圈1～6為抽籤位置，非固定池排名。"
                },
            },
            {
                "id": "source:school:encouragement-slots",
                "status": "8個入籤來源及參賽身分待核；未指派選手",
                "procedure": {
                    "description": "內部連線依原稿第19頁；8個入籤來源待確認，不推定為主賽首輪敗者。"
                },
            },
        ],
        "rule_summaries": [
            {
                "title": "原稿證據樣張範圍",
                "lines": [
                    "僅供審閱，尚未核准正式發布；原稿未載場地不得補入場地A～D。",
                    "男子B保留原表9列（含第195場）；原圖SyntheticAlias007對SyntheticPlayer002連線未編號，不自行補成正式場次。",
                    "男子F保留三池各取二及原稿圈1～6籤位；未指派晉級選手至任何籤位。",
                    "國中男子主賽與鼓勵獎依原稿連線重建；鼓勵獎8個入籤來源待核，不指定主賽敗者。",
                ],
            }
        ],
        "source_notes": notes,
        "metadata": {
            "source_file": source.name,
            "source_sha256": digest,
            "source_pages": [1, 8, 9, 18, 19, 36],
            "notice_source_page": 36,
            "notice_qr_decoding": "local_pymupdf_3x_zxingcpp_no_network",
            "notice_qr_url": qr_url,
            "notice_sponsor_scope": "historical_source_sample_only",
            "participant_identities_approved": False,
            "draw_assignments_approved": False,
            "schedule_approved": False,
            "school_topology": "printed_internal_relations_p18_p19",
            "school_final_and_bronze_labels_crosscheck_page": 33,
            "encouragement_initial_slots_assigned": False,
        },
    }
    model["content_hash"] = hashlib.sha256(
        json.dumps(model, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return model
