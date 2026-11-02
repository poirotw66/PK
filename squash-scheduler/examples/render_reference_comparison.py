"""Render the existing anonymous snapshot with the historical visual template.

Run from the project root:
    .venv/Scripts/python.exe -X utf8 examples/render_reference_comparison.py

The original PDF supplies review thumbnails only. The new booklet is built from
the existing snapshot through the document exporter; no source PDF page is used
as its background or as an output page. Existing releases are read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from html import escape
from pathlib import Path

import pymupdf

from squash_scheduler.domain.models import Snapshot, canonical_hash, content_hash
from squash_scheduler.exporters.assets import import_asset
from squash_scheduler.exporters.documents import build_documents, render_html, render_pdf
from squash_scheduler.exporters.reference_layout import schedule_cells
from squash_scheduler.storage.repository import atomic_json

PROJECT = Path(__file__).resolve().parents[1]
GRAPH_FIELDS = (
    "participants",
    "registrations",
    "entries",
    "stages",
    "pools",
    "matches",
    "assignments",
    "draw_runs",
    "results",
    "ranking_decisions",
)


def graph_hash(snapshot: Snapshot) -> str:
    data = snapshot.model_dump(mode="json")
    return canonical_hash({key: data.get(key) for key in GRAPH_FIELDS})


def _url(path: Path, output: Path) -> str:
    try:
        return Path(os.path.relpath(path, output)).as_posix()
    except ValueError:  # A caller may keep evidence on another Windows drive.
        return path.as_uri()


def _figure(path: Path, caption: str, output: Path) -> str:
    return (
        '<figure><a href="' + escape(_url(path, output), quote=True) + '">'
        '<img loading="lazy" src="'
        + escape(_url(path, output), quote=True)
        + '" alt="'
        + escape(caption, quote=True)
        + '"></a><figcaption>'
        + escape(caption)
        + "</figcaption></figure>"
    )


def _before_bundle(snapshot_path: Path) -> Path | None:
    report_path = snapshot_path.parent / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text("utf-8-sig"))
        bundle = snapshot_path.parent / "tournament" / "releases" / report["release_id"]
        if (bundle / "booklet.pdf").is_file():
            return bundle
    return None


def _graph_previews(bundle: Path, destination: Path) -> list[Path]:
    """Create review images without writing to an existing release."""
    previews = []
    with pymupdf.open(bundle / "booklet.pdf") as document:
        graph_indices = [
            index
            for index, page in enumerate(document)
            if "對戰圖" in page.get_text() and "svg" not in page.get_text()
        ]
        for index in graph_indices or list(range(min(2, len(document)))):
            target = destination / f"before-page-{index + 1:03}.png"
            document[index].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(target)
            previews.append(target)
    return previews


def render_historical_evidence(output: Path, source_pdf: Path, asset_id: str) -> dict:
    """Render source display evidence without creating accepted participant IDs.

    Only the printed F-group graph relations on source pages 8 and 9 are
    reconstructed. The six circled finals positions remain generic draw slots;
    no qualifying participant or pool rank is assigned to any of those slots.
    Numbered rows and separate unnumbered exhibitions remain published evidence.
    """
    published_path = PROJECT / "output/historical-schedule/published-schedule.json"
    published = json.loads(published_path.read_text("utf-8-sig"))
    if published["source_sha256"] != hashlib.sha256(source_pdf.read_bytes()).hexdigest():
        raise ValueError("Historical schedule evidence refers to a different source PDF")
    directory = output / "historical"
    directory.mkdir(exist_ok=True)
    originals = {row["display_code"]: row for row in published["rows"]}
    division_id, pool_stage, final_stage = "evidence:男子F組", "evidence:F:pool", "evidence:F:final"
    pools, entry_refs, slot_refs = [], {}, {}
    prelim_codes = set()
    for label, codes in zip(
        "ABC", (("44", "63", "82"), ("45", "64", "83"), ("46", "65", "84")), strict=True
    ):
        top, left = originals[codes[0]]["side_a_display"], originals[codes[0]]["side_b_display"]
        right = originals[codes[1]]["side_b_display"]
        # SVG vertices run clockwise: top, right, left. Names remain source strings.
        entries = [
            {"entry_id": f"evidence:F:{label}:{index}", "label": name}
            for index, name in enumerate((top, right, left))
        ]
        pools.append(
            {
                "id": f"evidence:F:{label}",
                "stage_id": pool_stage,
                "label": label,
                "advancement_count": 2,
                "entries": entries,
            }
        )
        for entry in entries:
            entry_refs[entry["label"]] = {"type": "entry", "entry_id": entry["entry_id"]}
        prelim_codes.update(codes)
    for index in range(1, 7):
        slot_refs[str(index)] = {
            "type": "draw_slot",
            "slot_id": str(index),
            "draw_run_id": "evidence:printed-generic-slots",
        }

    def slot(index: int) -> tuple[str, dict]:
        return chr(0x245F + index), slot_refs[str(index)]

    def dependency(code: str, loser: bool = False) -> tuple[str, dict]:
        return code + (" 敗者" if loser else " 勝者"), {
            "type": "match_loser" if loser else "match_winner",
            "match_id": "evidence:match:" + code,
        }

    final_sides = {
        "185": (slot(2), slot(3)),
        "186": (slot(4), slot(5)),
        "204": (slot(1), dependency("185")),
        "205": (dependency("186"), slot(6)),
        "220": (dependency("204"), dependency("205")),
        "221": (dependency("204", True), dependency("205", True)),
    }
    source_rows = [(row, row["display_code"]) for row in published["rows"]]
    source_rows.extend(
        (row, f"未編號{index}")
        for index, row in enumerate(published.get("unnumbered_exhibitions", []), 1)
    )
    matches = []
    for source, code in source_rows:
        is_prelim, is_final = code in prelim_codes, code in final_sides
        stage_id = pool_stage if is_prelim else final_stage if is_final else "evidence:table-only"
        sides = final_sides.get(code)
        if sides:
            a, b = sides
        else:
            a_name, b_name = source.get("side_a_display"), source.get("side_b_display")
            a_ref = (
                entry_refs.get(a_name, {"type": "unknown"}) if is_prelim else {"type": "unknown"}
            )
            b_ref = (
                entry_refs.get(b_name, {"type": "unknown"}) if is_prelim else {"type": "unknown"}
            )
            a = (a_name or "待確認", a_ref)
            b = (b_name or "待確認", b_ref)
        matches.append(
            {
                "match_id": "evidence:match:" + code,
                "code": code,
                "division_id": division_id
                if is_prelim or is_final
                else "evidence:" + source["division_label"],
                "division": "男子F組" if is_prelim or is_final else source["division_label"],
                "stage_id": stage_id,
                "stage": "預賽"
                if is_prelim
                else "決賽"
                if is_final
                else source.get("round_label") or "",
                "kind": "季殿／名次賽"
                if code == "221"
                else "表演賽"
                if source["display_code"] is None
                else "原稿賽程",
                "side_a": a[0],
                "side_b": b[0],
                "side_a_ref": a[1],
                "side_b_ref": b[1],
                "date": source["date"],
                "time": source["local_time"] or "原稿未載",
                "court": source.get("court") or "原稿未載",
                "court_order": 0,
                "round": 1
                if code in {"185", "186"} or is_prelim
                else 2
                if code in {"204", "205"}
                else 3
                if is_final
                else None,
                "position": {"185": 1, "186": 2, "204": 0, "205": 1, "220": 0, "221": 1}.get(
                    code, 0 if is_prelim else None
                ),
                "sources": [ref["match_id"] for ref in (a[1], b[1]) if "match_id" in ref],
                "evidence_status": "draft_evidence",
                "published_source": source,
                "display_code_is_review_label": source["display_code"] is None,
            }
        )
    model = {
        "schema_version": "1.0.0",
        "name": "2026中正盃歷史排版核對草案",
        "public_name": "2026 中正盃壁球錦標賽",
        "venue": "來源未列",
        "timezone": "Asia/Taipei",
        "draft": True,
        "revision_id": "historical-evidence-no-approved-revision",
        "content_hash": canonical_hash(published),
        "publication": {
            "layout_template_id": "reference-v1",
            "paper_size": "A4",
            "orientation": "landscape",
            "font_asset_id": asset_id,
            "historical_layout_verified": False,
            "qr_mode": "disabled",
            "sponsor_mode": "none",
            "sponsors": [],
        },
        "stages": [
            {"id": pool_stage, "division_id": division_id, "type": "round_robin", "order": 0},
            {"id": final_stage, "division_id": division_id, "type": "elimination", "order": 1},
        ],
        "pools": pools,
        "matches": matches,
        "issues": [],
        "issue_history": [],
        "changes": [],
        "roster_candidates": [],
        "rule_summaries": [
            {
                "title": "來源與用途",
                "lines": [
                    "DRAFT 歷史排版證據草案，不可作正式公告。",
                    "姓名與場次保留原稿顯示內容；未建立已確認參賽者身分。",
                    "僅男子F組對戰圖依原始PDF第8、9頁已印出的線條關係重新繪製。",
                    "決賽圈1至圈6為原稿抽籤位置；未指定任何選手或小組名次。",
                    "原稿註記：小組第一名抽圈1、圈3、圈6。此為歷史文字證據，未執行抽籤。",
                    f"每日表保留已出版賽程全部{len(matches)}筆；球場未記載時顯示「原稿未載」。",
                    "未編號1、未編號2是兩筆表演賽的審閱標籤，不是原稿正式場次編號；時間原稿未載。",
                    "未推定真實賽事規則、球場、晉級抽籤結果或公告核准。",
                ],
            }
        ],
        "evidence": {
            "status": "draft_evidence",
            "source_file": source_pdf.name,
            "source_sha256": published["source_sha256"],
            "graph_pages": [8, 9],
            "schedule_source": str(published_path),
            "identities_approved": False,
            "printed_slot_note": "小組第一名抽：① ③ ⑥",
            "numbered_row_count": len(published["rows"]),
            "unnumbered_exhibition_count": len(published.get("unnumbered_exhibitions", [])),
            "draw_assignments_approved": False,
            "schedule_approved": False,
        },
    }
    atomic_json(directory / "render-model.json", model)
    html = render_html(model, directory, asset_root=output / "assets")
    (directory / "review.html").write_text(html, "utf-8")
    atomic_json(directory / "render-model.json", model)
    renderer = render_pdf(html, directory / "booklet.pdf")
    previews = []
    with pymupdf.open(directory / "booklet.pdf") as document:
        text = "".join(page.get_text() for page in document)
        normalized = "".join(text.split())
        for name in entry_refs:
            if name not in text:
                raise ValueError("Historical evidence PDF lost a displayed name: " + name)
        for row in matches:
            cells = schedule_cells(row)
            marker = "".join(cells[0].split())
            if normalized.count(marker) != 1:
                raise ValueError(
                    "Historical evidence PDF must contain each schedule marker once: " + marker
                )
            for value in cells[1:]:
                if "".join(str(value).split()) not in normalized:
                    raise ValueError(
                        "Historical evidence PDF lost a displayed schedule value: " + str(value)
                    )
        for index, page in enumerate(document):
            preview = directory / f"page-{index + 1:03}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(preview)
            previews.append(
                {
                    "page": index + 1,
                    "preview": preview.name,
                    "width": page.rect.width,
                    "height": page.rect.height,
                }
            )
    report = {
        "status": "draft_evidence",
        "match_count": len(matches),
        "pages": previews,
        "numbered_match_count": len(published["rows"]),
        "unnumbered_exhibition_count": len(published.get("unnumbered_exhibitions", [])),
        "visible_schedule_markers_verified": len(matches),
        "source_sha256": published["source_sha256"],
        "renderer": renderer,
        "visual_review": "required",
        "historical_layout_verified": False,
        "unknown_courts_preserved": all(
            row["court"] == "原稿未載"
            for row in matches
            if row["published_source"].get("court") is None
        ),
        "identities_approved": False,
        "draw_assignments_approved": False,
        "schedule_approved": False,
    }
    atomic_json(directory / "evidence-report.json", report)
    from squash_scheduler.exporters.portal import write_portal

    write_portal(model, directory)
    return report


def render_comparison(
    output: Path, snapshot_path: Path, source_pdf: Path, font: Path, *, resume: bool = False
) -> dict:
    output, snapshot_path, source_pdf, font = (
        path.resolve() for path in (output, snapshot_path, source_pdf, font)
    )
    for path in (snapshot_path, source_pdf, font):
        if not path.is_file():
            raise FileNotFoundError(path)
    if output.exists() and not resume:
        raise FileExistsError(f"Choose a fresh output directory: {output}")
    original = Snapshot.model_validate_json(snapshot_path.read_text("utf-8-sig"))
    snapshot = original.model_copy(deep=True)
    output.mkdir(parents=True, exist_ok=resume)
    font_asset = import_asset(output, font, "font")
    snapshot.config["event"]["name"] = "匿名測試賽事"
    for division in snapshot.divisions:
        division.name = division.name.replace(" ", "")
    snapshot.config["publication"].update(
        layout_template_id="reference-v1",
        paper_size="A4",
        orientation="landscape",
        font_asset_id=font_asset["asset_id"],
        historical_layout_verified=False,
    )
    if graph_hash(snapshot) != graph_hash(original):
        raise AssertionError("Presentation changes unexpectedly altered tournament data")
    atomic_json(output / "presentation.snapshot.json", snapshot.model_dump(mode="json"))
    manifest_path = output / "after/manifest.json"
    if resume and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text("utf-8-sig"))
        if manifest["content_hash"] != content_hash(snapshot) or manifest["status"] != "draft":
            raise ValueError("Resume requires the same presentation snapshot and a draft manifest")
    else:
        manifest = build_documents(
            snapshot, output / "after", draft=True, asset_root=output / "assets"
        )
    historical = render_historical_evidence(output, source_pdf, font_asset["asset_id"])

    reference_images = []
    with pymupdf.open(source_pdf) as document:
        for index in (7, 8):
            target = output / f"original-page-{index + 1:03}.png"
            document[index].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(target)
            reference_images.append(target)
    before = _before_bundle(snapshot_path)
    before_images = _graph_previews(before, output) if before else []
    after_images = [output / "after" / page["preview"] for page in manifest["qa"]["pages"]]

    def link(path: Path, label: str) -> str:
        return '<a href="' + escape(_url(path, output), quote=True) + '">' + escape(label) + "</a>"

    columns = [
        "<section><h2>原始排版參考</h2><p>"
        + link(source_pdf, "開啟原始 PDF")
        + "（第 8、9 頁）</p>"
        + "".join(
            _figure(path, f"原始第 {index + 8} 頁", output)
            for index, path in enumerate(reference_images)
        )
        + "</section>",
        "<section><h2>修改前：standard-v1</h2><p>"
        + (link(before / "booklet.pdf", "開啟既有合成範例 PDF") if before else "找不到既有範例")
        + "</p><p>保留既有版本。下方顯示對戰圖頁；完整版本與規則頁請開啟 PDF。</p>"
        + "".join(_figure(path, path.stem, output) for path in before_images)
        + "</section>",
        "<section><h2>修改後：reference-v1</h2><p>"
        + link(output / "after" / "index.html", "開啟匿名範例前台")
        + " · "
        + link(output / "after" / "booklet.pdf", "開啟新排版 PDF")
        + " · "
        + link(output / "after" / "review.html", "開啟新排版 HTML")
        + "</p><p>從相同合成名單、場次、晉級來源與排程重新繪製，使用受控標楷體。</p>"
        + "".join(
            _figure(path, f"新排版第 {index + 1} 頁", output)
            for index, path in enumerate(after_images)
        )
        + "</section>",
    ]
    html = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>原始排版與新模板比較 · 草案</title><style>
body{font:16px "Microsoft JhengHei",sans-serif;margin:24px;color:#222;background:#f4f4f2}
h1{font-size:26px}h2{font-size:21px}p{line-height:1.7}a{color:#154e79}
.notice{border:2px solid #8b3c20;padding:14px;background:#fff6ee}
main{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;align-items:start}
section{min-width:0;background:white;padding:16px;border:1px solid #bbb}
figure{margin:20px 0}img{width:100%;border:1px solid #ccc}figcaption{font-size:13px;color:#555}
@media(max-width:1000px){main{grid-template-columns:1fr}}
</style><h1>原始排版 → 修改前 → 新模板</h1>
<p class="notice">DRAFT 草案／合成資料。這是排版比較，不是正式公告，也未核准真實賽事的身分、抽籤、晉級或排程。
原始第 8、9 頁供版面與歷史圖關係核對；新 PDF 由程式繪製，未貼上原始 PDF 頁面。</p>
<p>新模板採黑白、楷體、三角循環賽、由下往上的決賽樹及獨立每日賽程。
原始賽事的姓名與場次和匿名範例不同；請比較字型、線條、留白與資訊位置。
匿名範例的晉級來源沿用既有資料，未為了外觀更動對戰關係。</p><main>"""
    historical_html = (
        "<section><h2>另附：原稿真實文字的歷史排版證據草案</h2><p>"
        + link(output / "historical/index.html", "開啟歷史排版前台")
        + " · "
        + link(output / "historical/booklet.pdf", "開啟歷史排版草案 PDF")
        + " · "
        + link(output / "historical/review.html", "開啟歷史排版草案 HTML")
        + "</p><p>男子F組三個循環賽與六個決賽位置依原稿第8、9頁重新繪製；"
        "全部232筆已出版賽程作為顯示證據。未確認身分或指定決賽選手；球場未載處保留未知。</p>"
        + "".join(
            _figure(
                output / "historical" / page["preview"],
                f"歷史排版證據草案第 {page['page']} 頁",
                output,
            )
            for page in historical["pages"][:2]
        )
        + "</section>"
    )
    (output / "index.html").write_text(
        html + "".join(columns) + "</main>" + historical_html + "</html>", "utf-8"
    )
    report = {
        "status": "draft_visual_comparison",
        "synthetic_only": False,
        "synthetic_bundle": "after",
        "historical_evidence_bundle": "historical",
        "source_pdf": str(source_pdf),
        "source_sha256": hashlib.sha256(source_pdf.read_bytes()).hexdigest(),
        "reference_pages": [8, 9],
        "seed_snapshot": str(snapshot_path),
        "seed_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "graph_hash": graph_hash(snapshot),
        "graph_data_preserved": True,
        "before_bundle": str(before) if before else None,
        "after_bundle": str(output / "after"),
        "review": str(output / "index.html"),
        "synthetic_frontend": str(output / "after/index.html"),
        "historical_frontend": str(output / "historical/index.html"),
        "font_asset_id": font_asset["asset_id"],
        "qa": manifest["qa"],
        "historical_evidence": historical,
        "historical_layout_verified": False,
    }
    atomic_json(output / "comparison-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "output/reference-layout-example")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=PROJECT / "output/formal-demo-current/nine-entries.snapshot.json",
    )
    parser.add_argument(
        "--source-pdf", type=Path, default=PROJECT.parent / "2026中正盃20261023.pdf"
    )
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/kaiu.ttf"))
    parser.add_argument(
        "--historical-only",
        action="store_true",
        help="Add the historical evidence booklet to an existing comparison workspace",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume this example's draft workspace after a partial rendering failure",
    )
    parser.add_argument(
        "--geometry-check",
        type=Path,
        help="Render an existing view model as a separate draft geometry check",
    )
    args = parser.parse_args()
    if args.geometry_check:
        args.output.mkdir(parents=True, exist_ok=False)
        asset = import_asset(args.output, args.font, "font")
        model = json.loads(args.geometry_check.read_text("utf-8-sig"))
        model["draft"] = True
        model["publication"]["font_asset_id"] = asset["asset_id"]
        html = render_html(model, args.output, asset_root=args.output / "assets")
        (args.output / "review.html").write_text(html, "utf-8")
        atomic_json(args.output / "render-model.json", model)
        renderer = render_pdf(html, args.output / "booklet.pdf")
        print(
            json.dumps(
                {"status": "draft_geometry_check", "path": str(args.output), "renderer": renderer},
                ensure_ascii=False,
            )
        )
        return
    if args.historical_only:
        args.output.mkdir(parents=True, exist_ok=True)
        asset = import_asset(args.output, args.font, "font")
        report = render_historical_evidence(
            args.output.resolve(), args.source_pdf.resolve(), asset["asset_id"]
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "match_count": report["match_count"],
                    "path": str(args.output / "historical"),
                },
                ensure_ascii=False,
            )
        )
        return
    report = render_comparison(
        args.output, args.snapshot, args.source_pdf, args.font, resume=args.resume
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "review": report["review"],
                "after_bundle": report["after_bundle"],
                "graph_data_preserved": report["graph_data_preserved"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
