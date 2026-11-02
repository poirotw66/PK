"""Rebuild a review-only content baseline; never changes tournament identities.

Run: .venv/Scripts/python.exe -X utf8 examples/compare_booklet_baseline.py
The PDF is opened again, not trusted solely through a cached text extraction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from html import escape
from pathlib import Path

import pymupdf

PROJECT = Path(__file__).resolve().parents[1]
# These are division display labels only. Never apply this map to names or IDs.
GROUP_ALIASES = {
    **{f"男子{x}": f"男子{x}組" for x in "ABCDEF"},
    **{f"女子{x}": f"女子{x}組" for x in "ABCDE"},
    **{
        f"{age}{sex}": f"{age}{sex}子組" for age in ("高中", "國中", "國小") for sex in ("男", "女")
    },
}
PENDING_RULES = [
    "主辦／承辦／協辦、賽事日期地點與聯絡窗口",
    "參賽資格、分組、報名與費用、報到及退賽",
    "各組賽制、分池、取幾名、籤位／抽籤程序、輪空及鼓勵賽",
    "計分局數、積分、同分判定、遲到、棄權與受傷規則",
    "場地實際開放時段、休息、緩衝及兼報衝突處理",
    "獎勵、申訴、保險／安全與不可抗力處理",
    "核准通知網址／QR、贊助素材及版本核准人",
]


def read(path: Path):
    return json.loads(path.read_text("utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def group_label(value: str) -> str:
    return GROUP_ALIASES.get(value, value)


def compact(text: str) -> str:
    """For PDF extraction verification only; not participant identity matching."""
    return "".join(text.split())


def compare_counts(original: list[dict], new: list[dict]) -> dict:
    """Compare row counts; codes belong to separate namespaces, not a join key."""
    return {
        "original_rows": len(original),
        "original_numbered_rows": sum(str(m["code"]).isdigit() for m in original),
        "original_unnumbered_rows": sum(not str(m["code"]).isdigit() for m in original),
        "new_matches": len(new),
        "delta": len(new) - len(original),
        "original_printed_stage_labels": dict(
            Counter(m.get("stage") or "原表未標階段" for m in original)
        ),
        "new_stages": dict(Counter(m.get("stage") or "未標階段" for m in new)),
        "new_kinds": dict(Counter(m.get("kind") or "未標類型" for m in new)),
        "original_unknown_courts": sum(m.get("raw_court") is None for m in original),
        "original_unknown_times": sum(not m.get("time") for m in original),
        "new_courts": dict(Counter(m.get("court") or "未排場地" for m in new)),
    }


def verify_evidence(source: Path, published: dict, graphs: dict, summary: dict) -> dict:
    digest = sha(source)
    expected = {summary["sha256"]}
    expected.update(g["source_sha256"] for g in graphs.values())
    expected.update(m["source_ref"]["sha256"] for m in published["matches"])
    if expected != {digest}:
        raise ValueError("PDF SHA-256 differs from schedule, graph or comparison evidence")
    pages, checked = {}, 0
    with pymupdf.open(source) as doc:
        if len(doc) != summary["page_count"]:
            raise ValueError("PDF page count differs from comparison evidence")
        for row in published["matches"]:
            ref = row["source_ref"]
            extracted = doc[ref["page"] - 1].get_text(clip=pymupdf.Rect(ref["bbox"]))
            if compact(extracted) != compact(ref["text"]):
                raise ValueError(f"Source row text differs: {row['code']}")
            # Verify displayed names and code against that actual source row.
            for field in ("side_a", "side_b"):
                if row.get(field) and compact(row[field]) not in compact(extracted):
                    raise ValueError(f"Source display differs: {row['code']} {field}")
            if str(row["code"]).isdigit() and row["code"] not in extracted.split():
                raise ValueError(f"Source code differs: {row['code']}")
            checked += 1
        for graph in graphs.values():
            number = graph["source_page"]
            text = doc[number - 1].get_text()
            if compact(graph["division"]) not in compact(text):
                raise ValueError(f"Graph division differs on source page {number}")
            codes = re.findall(r"(?m)^\s*(\d+)\s*$", text)
            # Candidate numbers may also be seed/rank labels. Keep them explicit.
            pages[number] = {
                **graph,
                "extracted_text": text,
                "standalone_number_candidates": codes,
                "number_candidates_are_not_confirmed_match_count": True,
            }
    return {
        "source_file": source.name,
        "source_sha256": digest,
        "page_count": summary["page_count"],
        "verified_schedule_row_clips": checked,
        "graph_pages": pages,
        "verification": "actual_pdf_sha256_and_fresh_text_clips",
    }


def build_report(project: Path, source: Path) -> dict:
    original_path = project / "output/full-event-demo/publication-model.json"
    graphs_path = project / "output/full-event-demo/graph-metadata.json"
    model_path = project / "output/full-demo-four-courts/render-model.json"
    assumptions_path = project / "output/full-demo-four-courts/assumptions.json"
    differences_path = project / "output/full-event-demo/source-differences.json"
    summary_path = project / "output/pdf-comparison/original-summary.json"
    original, graphs, new, assumptions, differences, summary = (
        read(p)
        for p in (
            original_path,
            graphs_path,
            model_path,
            assumptions_path,
            differences_path,
            summary_path,
        )
    )
    if original.get("graph_metadata") != graphs:
        raise ValueError("Publication graph metadata differs from standalone graph evidence")
    evidence = verify_evidence(source, original, graphs, summary)
    groups = list(dict.fromkeys(r["division"] for r in original["roster"] + new["roster"]))
    for match in original["matches"] + new["matches"]:
        label = group_label(match["division"])
        if label not in groups:
            groups.append(label)
    rows = []
    for label in groups:
        old_matches = [m for m in original["matches"] if group_label(m["division"]) == label]
        new_matches = [m for m in new["matches"] if group_label(m["division"]) == label]
        old_roster = [r for r in original["roster"] if r["division"] == label]
        new_roster = [r for r in new["roster"] if r["division"] == label]
        graph_pages = [g for g in evidence["graph_pages"].values() if g["division"] == label]
        stage_ids = {m["stage_id"] for m in new_matches}
        pools = [p for p in new["pools"] if p["stage_id"] in stage_ids]
        names = {r["name"] for r in old_roster}
        displays = {str(m[k]) for m in old_matches for k in ("side_a", "side_b") if m.get(k)}
        unknown_displays = sorted(displays - names)
        code_set = {str(m["code"]) for m in old_matches if str(m["code"]).isdigit()}
        graph_numbers = {code for g in graph_pages for code in g["standalone_number_candidates"]}
        notes = [
            "原稿與DEMO為不同抽籤／賽制；場次編號不得逐號當作同一場。",
            "原稿圖頁僅作顯示證據，未將圖線或名次完整轉成核准賽事模型。",
        ]
        if any("鼓勵" in g["stage"] for g in graph_pages):
            notes.append("原稿有主賽及鼓勵獎；DEMO分池／淘汰／季殿不是原稿鼓勵賽，勝敗來源待核。")
        if label == "男子B組":
            notes.append("原稿圖頁數字與時間表有差異，需核對下列候選；不得自行補成已公布場次。")
        if label == "男子F組":
            notes.append("原稿小組第一名抽①③⑥；DEMO採固定池排名，抽位語義不同，待核准。")
        if not old_matches and old_roster:
            notes.append("原稿未列該組正式場次；保留報名，不虛構對手或歷史時間。")
        rows.append(
            {
                "division": label,
                "counts": compare_counts(old_matches, new_matches),
                "original_registration_count": len(old_roster),
                "new_registration_count": len(new_roster),
                "original_graph_pages": graph_pages,
                "original_graph_number_candidates_absent_from_schedule": sorted(
                    graph_numbers - code_set, key=int
                ),
                "graph_number_difference_status": "candidate_only_requires_visual_check",
                "new_pools": [
                    {
                        "label": p["label"],
                        "entry_count": len(p["entries"]),
                        "advancement_count": p["advancement_count"],
                    }
                    for p in pools
                ],
                "new_stages": [s for s in new["stages"] if s["id"] in stage_ids],
                "pdf_displays_absent_from_exact_group_roster": unknown_displays,
                "group_roster_names_absent_from_exact_schedule_display": sorted(names - displays),
                "roster_name_differences": {
                    "only_original": sorted(names - {r["name"] for r in new_roster}),
                    "only_new": sorted({r["name"] for r in new_roster} - names),
                },
                "original_matches": old_matches,
                "new_matches": new_matches,
                "notes": notes,
            }
        )
    return {
        "schema_version": "1.0.0",
        "status": "review_only_not_release_approval",
        "inputs": {
            str(p.relative_to(project)): sha(p)
            for p in (
                original_path,
                graphs_path,
                model_path,
                assumptions_path,
                differences_path,
                summary_path,
            )
        },
        "source_verification": evidence,
        "group_alias_policy": {
            "mapping": GROUP_ALIASES,
            "purpose": "division_display_grouping_only",
            "participant_identity_aliasing": False,
        },
        "identity_decision_from_source": original["identity_decision"],
        "identity_policy": "沿用既有同字同名核定；不去姓名空格、不推定SyntheticAlias007等別名、不更動參賽者ID。",
        "totals": compare_counts(original["matches"], new["matches"]),
        "source_differences": differences,
        "assumptions": assumptions,
        "groups": rows,
        "approval": {
            "official_publication": "未核准；兩份輸入皆為草稿",
            "original_draft": original["draft"],
            "new_draft": new["draft"],
            "confirmed_scope": assumptions["confirmed_by_user"],
            "historical_courts": "原稿未載；四場地僅供新規劃，不可倒填原稿",
            "formats_and_draw": "待主辦逐組核准，DEMO不等於原稿賽制",
            "schedule_and_rules": "DEMO假設，待核准實際時段、休息、計分與棄權等規則",
            "visual_review": "待人工審查；本報告不核准版型或正式發布",
        },
        "pending_official_regulations": PENDING_RULES,
        "limitations": [
            "原稿是對戰圖、時間表與通知，未附完整競賽辦法。",
            "圖頁數字可能是籤位，候選差異不等於已確認漏賽；不得自動補賽。",
            "人數依Word名冊；原稿PDF圖中文字不是已核准名單身分。",
            "兩版本重新編排，未建立逐場一對一對應，時間僅比較各自公布值與缺值。",
        ],
    }


def table(headers: list[str], rows: list[list]) -> str:
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join("<th>" + escape(str(h)) + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + escape(str(c)) + "</td>" for c in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def render_html(report: dict) -> str:
    total = report["totals"]
    body = '<h1>逐組內容基準與差異表</h1><p class="notice">審閱草稿・正式發布未核准</p>'
    body += f"<p>原稿 {total['original_rows']} 列（編號 {total['original_numbered_rows']}、未編號 {total['original_unnumbered_rows']}） → 新編 DEMO {total['new_matches']} 場。兩版本為不同抽籤與賽制。</p>"
    body += "<p>原稿場地未載，保留原稿未載；場地A～D只屬於新規劃。姓名逐字保留；組別縮稱僅用於分組顯示，不視為參賽者別名。</p>"
    body += '<p><a href="../pdf-comparison/PDF對比與正式簡章修正建議.html">既有PDF對比報告</a> · <a href="baseline.json">完整JSON及來源逐列資料</a></p>'
    body += "<h2>逐組場次與賽制</h2>" + table(
        ["組別", "名冊原→新", "原表列數", "新編場次", "差額", "原稿圖頁／階段", "模擬階段"],
        [
            [
                g["division"],
                f"{g['original_registration_count']}→{g['new_registration_count']}",
                g["counts"]["original_rows"],
                g["counts"]["new_matches"],
                g["counts"]["delta"],
                "；".join(f"p{x['source_page']} {x['stage']}" for x in g["original_graph_pages"])
                or "未附圖",
                json.dumps(g["counts"]["new_stages"], ensure_ascii=False),
            ]
            for g in report["groups"]
        ],
    )
    for group in report["groups"]:
        body += "<details><summary>" + escape(group["division"]) + "・逐列核對</summary>"
        body += "".join("<p>" + escape(n) + "</p>" for n in group["notes"])
        body += (
            "<p>新編分池：" + escape(json.dumps(group["new_pools"], ensure_ascii=False)) + "</p>"
        )
        body += (
            "<p>名冊逐字差異："
            + escape(json.dumps(group["roster_name_differences"], ensure_ascii=False))
            + "</p>"
        )
        body += (
            "<p>PDF顯示但同組名冊逐字不符："
            + escape("、".join(group["pdf_displays_absent_from_exact_group_roster"]) or "無")
            + "。此項不作姓名別名認定。</p>"
        )
        body += (
            "<p>圖頁獨立數字未見於同組時間表（含籤位等候選數字，需人工核對）："
            + escape(
                "、".join(group["original_graph_number_candidates_absent_from_schedule"]) or "無"
            )
            + "</p>"
        )
        body += "<details><summary>原稿各圖頁重新抽取文字（分池／取幾名／籤位依原文核對）</summary>"
        for graph in group["original_graph_pages"]:
            body += (
                "<h3>原稿 p"
                + str(graph["source_page"])
                + "・"
                + escape(graph["stage"])
                + "</h3><pre>"
                + escape(graph["extracted_text"])
                + "</pre>"
            )
        body += "</details>"
        for title, key in [("原稿時間表", "original_matches"), ("新編DEMO時間表", "new_matches")]:
            body += (
                "<h3>"
                + title
                + "</h3>"
                + table(
                    ["場號", "日期", "時間", "場地", "階段", "選手甲／參照", "選手乙／參照"],
                    [
                        [
                            m["code"],
                            m.get("date") or "原稿未載",
                            m.get("time") or "原稿未載",
                            m.get("court") or "原稿未載",
                            m.get("stage") or "原表未標階段",
                            m.get("side_a") or "未載",
                            m.get("side_b") or "未載",
                        ]
                        for m in group[key]
                    ],
                )
            )
        body += "</details>"
    body += "<h2>待查姓名與時間</h2><p>" + escape(report["source_differences"]["policy"]) + "</p>"
    body += (
        "<pre>"
        + escape(json.dumps(report["source_differences"], ensure_ascii=False, indent=2))
        + "</pre>"
    )
    body += "<p>原稿兩筆未編號表演賽時間未載；未編號1／2是審閱標籤。不同抽籤沒有逐場對應，不把08:00等模擬時間認作原稿修正。</p>"
    body += "<h2>正式資料核准狀態</h2>" + table(["範圍", "狀態"], list(report["approval"].items()))
    body += (
        "<h2>完整章程待補</h2><ul>"
        + "".join("<li>" + escape(v) + "</li>" for v in report["pending_official_regulations"])
        + "</ul>"
    )
    body += (
        "<h2>新編假設與範圍限制</h2><pre>"
        + escape(json.dumps(report["assumptions"], ensure_ascii=False, indent=2))
        + "</pre>"
    )
    body += "".join("<p>" + escape(v) + "</p>" for v in report["limitations"])
    verify = report["source_verification"]
    body += (
        "<h2>來源驗證</h2><p>"
        + escape(
            f"{verify['source_file']}・{verify['page_count']}頁；逐列重新抽取並核對{verify['verified_schedule_row_clips']}筆PDF文字。SHA-256 {verify['source_sha256']}"
        )
        + "</p>"
    )
    body += "<pre>" + escape(json.dumps(report["inputs"], ensure_ascii=False, indent=2)) + "</pre>"
    return (
        """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>逐組內容基準與差異表</title><style>
body{font-family:"Microsoft JhengHei",sans-serif;background:#f0f4f2;color:#17382f;line-height:1.8;margin:0}main{max-width:1250px;margin:auto;padding:30px 22px}h1{border-top:5px solid #285846;padding-top:20px}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td,th{border:1px solid #c7d5cb;padding:10px;text-align:left;vertical-align:top}th{background:#285846;color:white}.scroll{overflow:auto}details{background:white;margin:15px 0;padding:15px;border:1px solid #c7d5cb}summary{cursor:pointer;font-size:20px;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;font-size:13px}.notice{background:#fff1c6;padding:14px}a{color:#1d6658}@media print{body{background:white}main{padding:0}details{break-inside:auto}}
</style><main>"""
        + body
        + "</main></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pdf", type=Path)
    parser.add_argument("--output", type=Path, default=PROJECT / "output/booklet-baseline")
    args = parser.parse_args()
    source = args.source_pdf or next(PROJECT.parent.glob("*20261023.pdf"))
    report = build_report(PROJECT, source)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (args.output / "index.html").write_text(render_html(report), "utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "totals": report["totals"],
                "groups": len(report["groups"]),
                "verified_rows": report["source_verification"]["verified_schedule_row_clips"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
