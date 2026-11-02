"""Map every original PDF page to generated pages using graph and timetable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from html import escape
from io import BytesIO
from pathlib import Path

import pymupdf
import zxingcpp
from PIL import Image

from squash_scheduler.storage.repository import atomic_json


def build(package: Path) -> dict:
    root = package.resolve()
    generated = root / "source-samples"
    model = json.loads((generated / "render-model.json").read_text("utf-8"))
    report = json.loads((generated / "review-report.json").read_text("utf-8"))
    original_path, target_path = root / "original.pdf", generated / "booklet.pdf"
    original, target = pymupdf.open(original_path), pymupdf.open(target_path)
    mapping = {int(k): set(v) for k, v in report["source_page_to_generated_pages"].items()}
    text = [page.get_text() for page in target]
    schedule_rows = []
    for row in model["matches"]:
        source_page = row["source_ref"]["page"]
        pages = [i + 1 for i, value in enumerate(text) if f"[{row['code']}]" in value]
        if len(pages) != 1:
            raise ValueError(f"Schedule row {row['code']} must appear on exactly one PDF page")
        mapping.setdefault(source_page, set()).update(pages)
        schedule_rows.append(
            {"code": row["code"], "source_page": source_page, "generated_page": pages[0]}
        )
    notices = []
    navigation = json.loads((generated / "navigation-map.json").read_text("utf-8"))
    notice_page = next((n["page"] for n in navigation["entries"] if n["target"] == "notices"), None)
    for missing in set(range(1, len(original) + 1)) - set(mapping):
        if notice_page and "場次通知" in original[missing - 1].get_text():
            pixmap = original[missing - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5))
            decoded = {
                r.text for r in zxingcpp.read_barcodes(Image.open(BytesIO(pixmap.tobytes("png"))))
            }
            url = model["publication"].get("qr_url")
            if url and url in decoded:
                mapping[missing] = {notice_page}
                notices.append(
                    {"source_page": missing, "generated_page": notice_page, "verified_qr_url": url}
                )
    if set(mapping) != set(range(1, len(original) + 1)):
        raise ValueError("Every original page must have an explicit generated-page mapping")
    directory = root / "page-comparison"
    directory.mkdir(exist_ok=False)
    sections = []
    for number, page in enumerate(original, 1):
        image = f"original-{number:03}.png"
        page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(str(directory / image))
        links = "".join(
            f'<figure><a href="../source-samples/page-{p:03}.png"><img loading="lazy" src="../source-samples/page-{p:03}.png" alt="新版第 {p} 頁"></a><figcaption>新版第 {p} 頁</figcaption></figure>'
            for p in sorted(mapping[number])
        )
        sections.append(
            f'<section id="page-{number}"><h2>原版第 {number} 頁 → 新版第 {"、".join(map(str, sorted(mapping[number])))} 頁</h2><div class="pair"><figure><a href="{image}"><img loading="lazy" src="{image}" alt="原版第 {number} 頁"></a><figcaption>原版第 {number} 頁</figcaption></figure><div>{links}</div></div></section>'
        )
    navigation = " ".join(f'<a href="#page-{p}">{p}</a>' for p in mapping)
    html = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>原版與新版逐頁比對</title><style>body{{margin:0;background:#f3f6f7;color:#181c20;font:14px/1.6 'Microsoft JhengHei',sans-serif}}main{{max-width:1480px;margin:auto;padding:28px}}h1{{font-size:26px}}h2{{font-size:18px;margin-top:32px}}nav{{display:flex;gap:8px;flex-wrap:wrap}}nav a{{padding:5px 10px;background:white;color:inherit}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}}figure{{margin:0 0 16px;background:white;border:1px solid #e8eaec}}img{{display:block;width:100%;height:auto}}figcaption{{padding:8px 12px;color:#5d5f60}}@media(max-width:700px){{main{{padding:18px}}.pair{{grid-template-columns:1fr}}}}</style><main><a href="../index.html">返回簡章審閱入口</a><h1>原版與新版逐頁比對</h1><p>原版 {len(original)} 頁逐頁對應新版 {len(target)} 頁。對戰圖依來源頁碼，時間表依逐場原文頁碼及新版場號定位；同一原頁可能拆成多頁，同一新頁也可能包含相鄰原頁的場次。點圖可放大。</p><p>{escape("新版統一圖形比例與字級，保留積分表及勝:2分 敗:1分 棄:0分。此為重新排版，未知籤位與正式條文仍不推定。")}</p><nav>{navigation}</nav>{"".join(sections)}</main></html>"""
    (directory / "index.html").write_text(html, "utf-8")
    evidence = {
        "original_page_count": len(original),
        "generated_page_count": len(target),
        "original_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
        "generated_sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
        "page_mapping": {str(k): sorted(v) for k, v in sorted(mapping.items())},
        "schedule_rows": schedule_rows,
        "mapped_schedule_rows": len(schedule_rows),
        "notices": notices,
    }
    atomic_json(directory / "mapping.json", evidence)
    entry = root / "index.html"
    if entry.exists():
        html = entry.read_text("utf-8")
        if 'href="page-comparison/index.html"' not in html:
            html = html.replace(
                "<main>",
                '<main><p><a href="page-comparison/index.html">原版 36 頁逐頁比對新版</a></p>',
                1,
            )
            entry.write_text(html, "utf-8")
    package_report = root / "package-report.json"
    if package_report.exists():
        value = json.loads(package_report.read_text("utf-8"))
        value["artifacts"] = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file() and p != package_report
        }
        atomic_json(package_report, value)
    original.close()
    target.close()
    return {
        "original_pages": evidence["original_page_count"],
        "generated_pages": evidence["generated_page_count"],
        "mapped_rows": len(schedule_rows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.package)))
