"""Build the complete new booklet, historical samples, and a single review entrance."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from html import escape
from pathlib import Path

from build_booklet_review import build as build_full
from build_page_comparison import build as build_comparison
from compare_booklet_baseline import build_report, render_html
from render_historical_booklet_samples import build as build_samples

from squash_scheduler.storage.repository import atomic_json


def package(source: Path, output: Path, content: Path | None = None) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Choose a fresh booklet review package directory")
    output.mkdir(parents=True)
    project = Path(__file__).resolve().parents[1]
    original = next(project.parent.glob("*20261023.pdf"))
    full = build_full(source, output / "full-event", content)
    historical = build_samples(source, output / "source-samples", all_groups=True)
    baseline = build_report(project, original)
    (output / "baseline.html").write_text(render_html(baseline), "utf-8")
    atomic_json(output / "baseline.json", baseline)
    shutil.copyfile(original, output / "original.pdf")
    old_demo = project.parent / "demo1.pdf"
    if old_demo.exists():
        shutil.copyfile(old_demo, output / "previous-demo.pdf")
    build_comparison(output)
    selected = [
        (
            label,
            f"source-samples/page-{historical['source_page_to_generated_pages'][str(page)][0]:03}.png",
        )
        for label, page in [
            ("原稿男子 B；含未編號來源線", 1),
            ("原稿男子 F 特殊抽位", 9),
            ("原稿國中男子主賽／季殿", 18),
            ("原稿鼓勵賽；入籤來源待核", 19),
        ]
    ]
    for label, page in zip(
        ("競賽辦法待補欄位", "合併後比賽規則", "單組循環圖", "三池預賽", "決賽", "四場地每日賽程"),
        full["sample_original_pages"],
        strict=True,
    ):
        selected.append((label, f"full-event/booklet/page-{page:03}.png"))
    gallery = "".join(
        '<figure><a href="'
        + escape(path, quote=True)
        + '"><img loading="lazy" src="'
        + escape(path, quote=True)
        + '" alt="'
        + escape(label, quote=True)
        + '"></a><figcaption>'
        + escape(label)
        + "</figcaption></figure>"
        for label, path in selected
    )
    missing = "".join(
        "<li>" + escape(field.replace("publication.prospectus.", "")) + "</li>"
        for field in full["missing_formal_content"]
    )
    html = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>中正盃・新版簡章審閱</title><style>
footer{{overflow-wrap:anywhere}}
*{{box-sizing:border-box}}body{{margin:0;background:#f3f4ef;color:#182e28;font:16px/1.8 "Microsoft JhengHei",sans-serif}}main{{max-width:1200px;margin:auto;padding:48px 24px 80px}}header{{border-top:5px solid #183e30;padding-top:20px}}.eyebrow{{letter-spacing:2px;font-size:13px}}h1{{font-size:clamp(28px,4vw,44px);margin:10px 0 18px;line-height:1.3}}h2{{font-size:24px;margin:36px 0 14px}}p{{max-width:900px}}.stats,.cards,.gallery{{display:grid;gap:18px;grid-template-columns:repeat(3,minmax(0,1fr))}}.stats{{margin:28px 0}}.stat,article{{background:#fff;padding:24px;border:1px solid #d5ddd3}}.stat strong{{display:block;font-size:32px}}.stat span{{font-size:14px}}article{{display:flex;flex-direction:column}}article h2{{font-size:21px;margin:0 0 10px}}article p{{font-size:14px;flex:1}}a{{color:#174f37}}.button{{display:block;text-decoration:none;padding:10px 14px;text-align:center;background:#224d38;color:white}}.notice{{border-left:4px solid #ba8b30;background:#fff4d7;padding:16px 22px}}.gallery{{grid-template-columns:repeat(2,minmax(0,1fr))}}figure{{margin:0;background:white;border:1px solid #d5ddd3;padding:12px}}figure img{{width:100%;height:340px;object-fit:contain}}figcaption{{padding:8px 2px;font-size:14px}}details{{background:white;padding:18px 22px;border:1px solid #d5ddd3}}summary{{cursor:pointer}}footer{{font-size:13px;border-top:1px solid #c1cfc0;margin-top:35px;padding-top:20px}}@media(max-width:740px){{main{{padding:26px 16px}}.cards,.gallery{{grid-template-columns:1fr}}.stats{{gap:8px}}.stat{{padding:12px}}.stat strong{{font-size:25px}}figure img{{height:auto}}}}
</style><main><header><div class="eyebrow">2026 中正盃 · PDF 改版審閱</div><h1>新版簡章，直接看成品。</h1><p>正式簡章欄位、原稿式對戰圖及四場地時間表已接入產生器。此包供主辦審閱；主辦條款、實際規則與發布核准仍需完成。</p></header>
<div class="stats"><div class="stat"><strong>108 → {full["page_count"]}</strong><span>完整模擬版 PDF 頁數</span></div><div class="stat"><strong>147 ／ 114</strong><span>報名筆數 ／ 選手人數</span></div><div class="stat"><strong>279 場</strong><span>四場地新編模擬賽程</span></div></div>
<div class="cards"><article><h2>先看代表頁</h2><p>新版競賽辦法、規則、循環圖、男子 F 三池與決賽、每日四場地時間表。取自完整 PDF。</p><a class="button" href="full-event/representative-pages.pdf">開啟代表頁 PDF</a></article>
<article><h2>完整新版簡章</h2><p>{full["page_count"]} 頁，保留全部 279 場與 147 筆報名；含三個未編對戰單人組的名單說明。共通規則集中一次。</p><a class="button" href="full-event/booklet/booklet.pdf">開啟完整 PDF</a></article>
<article><h2>原稿全組重建</h2><p>原稿 25 張圖、230 個編號場次與 2 筆表演賽，依來源資料重新繪製。含特殊抽位、主賽／季殿、鼓勵賽及 QR；未知入籤保留待核。</p><a class="button" href="source-samples/booklet.pdf">開啟原稿全組重建 PDF</a></article></div>
<p><a href="full-event/booklet/index.html">開啟新版閱讀前端</a>　·　<a href="full-event/booklet/schedule.xlsx">下載新版 Excel</a>　·　<a href="baseline.html">逐組原稿／新編差異表</a>　·　<a href="original.pdf">原版 36 頁 PDF</a></p>
<p><a href="source-samples/index.html">原稿全組重建閱讀前端</a>　·　<a href="source-samples/graph-catalog.html">25 張原圖的逐場來源證據</a></p>
<p class="notice">完整新版保留新抽籤的模擬賽程，數字場號依本版時間／場地重新編排，不代表原公布籤位。原稿重建另依 PDF 已畫出的關係繪製，保留原時間表欄序；鼓勵賽入籤來源未知，未擅自指定主賽敗者。男子 B 的第195場已列於原稿，SyntheticAlias007—SyntheticPlayer002未編號線僅作來源圖線，未補成正式場次。</p>
<p><a href="page-comparison/index.html">原版 36 頁逐頁比對新版</a></p>
<h2>實際 PDF 頁面</h2><div class="gallery">{gallery}</div>
<h2>正式發布前仍需補齊</h2><p>主辦／協辦、參賽資格、報名與報到、競賽辦法、獎勵、申訴及聯絡資訊。比賽時段、局數、休息、抽籤等示範設定仍須主辦核准；正式版也需逐頁與實印驗收。</p>
<details><summary>查看目前缺少的簡章設定欄位</summary><ul>{missing}</ul><p>填寫範本：examples/prospectus-content.template.json。設定 approved 不會讓「待補」文字或示範條款通過正式發布檢查。</p></details>
<footer>逐場 Excel／PDF 對照、對戰圖場次覆蓋、字級與頁面邊界檢查已通過。原稿來源 PDF SHA-256：{historical["source_sha256"]}。自動檢查不代替主辦對內容及全冊版面的核准。</footer></main></html>"""
    (output / "index.html").write_text(html, "utf-8")
    report = {
        "status": "draft_review_required",
        "full_event": full,
        "historical_samples": historical,
        "baseline_source_rows_verified": baseline["source_verification"][
            "verified_schedule_row_clips"
        ],
        "artifacts": {
            str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in output.rglob("*")
            if path.is_file()
        },
    }
    atomic_json(output / "package-report.json", report)
    return {
        "path": str(output),
        "full_event_pages": full["page_count"],
        "historical_sample_pages": historical["page_count"],
        "status": report["status"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("output/full-roster-simulation-delivery")
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prospectus-content", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.output, args.prospectus_content), ensure_ascii=True))
