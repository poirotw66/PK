"""Build a complete original-source reading package; no inferred historical courts."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from openpyxl import Workbook

from squash_scheduler.exporters.portal import write_portal
from squash_scheduler.importers.historical_graphs import (
    extract_source_graphs,
    roster_from_import_report,
)


def preserve_excel_strings(workbook: Workbook) -> None:
    """Source strings are literal evidence, never executable spreadsheet formulas."""
    for sheet in workbook:
        for cells in sheet:
            for cell in cells:
                if isinstance(cell.value, str):
                    cell.data_type = "s"


def build(project: Path, output: Path) -> dict:
    report = json.loads(
        (project / "output/historical-inventory/import-report.json").read_text("utf-8")
    )
    published = json.loads(
        (project / "output/historical-schedule/published-schedule.json").read_text("utf-8")
    )
    pdf = next(project.parent.glob("*20261023.pdf"))
    output.mkdir(parents=True, exist_ok=True)
    roster = roster_from_import_report(report)
    graphs = extract_source_graphs(pdf, output)
    matches = []
    for index, row in enumerate(published["rows"] + published.get("unnumbered_exhibitions", [])):
        matches.append(
            {
                "code": row.get("display_code") or f"未編號{index - len(published['rows']) + 1}",
                "date": row.get("date"),
                "time": row.get("local_time"),
                "court": row.get("court") or "原稿未載",
                "division": row.get("division_label") or row.get("division_round_text"),
                "stage": row.get("round_label") or "",
                "kind": "原公布賽程",
                "side_a": row.get("side_a_display"),
                "side_b": row.get("side_b_display"),
                "source_ref": row.get("source"),
                "raw_court": row.get("court"),
            }
        )
    model = {
        "name": "2026 中正盃壁球錦標賽",
        "public_name": "中正盃 · 完整原稿資料",
        "draft": True,
        "evidence": True,
        "roster": roster,
        "matches": matches,
        "graph_metadata": graphs,
        "courts": [
            {"id": label, "label": "場地" + label, "sort_order": i}
            for i, label in enumerate("ABCD")
        ],
        "coverage_note": "完整名冊 147 筆、17 組；原公布賽程 232 筆及原稿對戰圖 25 頁。男子A組、女子A組及女子B組各一人，原稿未附對戰圖。場地A／B／C／D為新規劃場地；歷史原稿未載場地，未推定配置。",
        "rule_summaries": [
            {
                "title": "原稿資料範圍",
                "lines": [
                    "此為原稿資料閱讀版，並非重新抽籤或新排賽程。",
                    "同字同名跨組依使用者確認視為同一人；保留姓名內空格，不合併不同寫法。",
                    "對戰圖完整保留原 PDF 的預賽、決賽、循環賽及鼓勵獎頁面。",
                ],
            }
        ],
        "identity_decision": {
            "actor": "Justin",
            "decision": "exact_name_confirmed",
            "scope": "同字同名跨組視為同一人",
            "aliases_inferred": False,
        },
        "publication_download_label": "原稿 PDF",
    }
    shutil.copyfile(pdf, output / "booklet.pdf")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "完整名冊"
    sheet.append(["姓名", "組別", "選手ID", "報名ID", "來源"])
    for row in roster:
        sheet.append(
            [
                row["name"],
                row["division"],
                row["participant_id"],
                row["registration_id"],
                json.dumps(row["source_ref"], ensure_ascii=False),
            ]
        )
    sheet = workbook.create_sheet("原公布賽程")
    sheet.append(["場號", "日期", "時間", "場地", "組別", "選手甲", "選手乙", "來源"])
    for row in matches:
        sheet.append(
            [
                row["code"],
                row["date"],
                row["time"],
                row["court"],
                row["division"],
                row["side_a"],
                row["side_b"],
                json.dumps(row["source_ref"], ensure_ascii=False),
            ]
        )
    sheet = workbook.create_sheet("來源對戰圖稽核")
    sheet.append(["圖檔", "組別", "階段", "原稿頁", "原稿SHA256", "SVG SHA256"])
    for row in graphs.values():
        sheet.append(
            [
                row["file"],
                row["division"],
                row["stage"],
                row["source_page"],
                row["source_sha256"],
                row["svg_sha256"],
            ]
        )
    preserve_excel_strings(workbook)
    workbook.save(output / "schedule.xlsx")
    roster_names = {row["name"] for row in roster}
    source_displays = {
        str(row[key]) for row in matches for key in ("side_a", "side_b") if row.get(key)
    }
    differences = {
        "policy": "Word名冊與PDF顯示文字逐字比較；未相同者僅列出，不推定別名或身份。PDF內容亦包含晉級佔位文字。",
        "identity_decision": model["identity_decision"],
        "pdf_displays_absent_from_exact_roster": sorted(source_displays - roster_names),
        "roster_names_absent_from_exact_schedule_display": sorted(roster_names - source_displays),
    }
    (output / "source-differences.json").write_text(
        json.dumps(differences, ensure_ascii=False, indent=2), "utf-8"
    )
    (output / "publication-model.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=2), "utf-8"
    )
    (output / "graph-metadata.json").write_text(
        json.dumps(graphs, ensure_ascii=False, indent=2), "utf-8"
    )
    write_portal(model, output)
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/full-event-demo"))
    args = parser.parse_args()
    result = build(Path(__file__).resolve().parents[1], args.output.resolve())
    print(
        json.dumps(
            {
                "roster": len(result["roster"]),
                "divisions": len({r["division"] for r in result["roster"]}),
                "matches": len(result["matches"]),
                "graphs": len(result["graph_metadata"]),
            }
        )
    )
