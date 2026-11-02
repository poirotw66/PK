"""Self-contained, offline spectator portal from the shared publication model."""

from __future__ import annotations

import base64
import json
from importlib.resources import files
from pathlib import Path
from xml.etree import ElementTree

from .schedule_rows import schedule_display


def write_portal(model: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    keys = ("code", "date", "time", "court", "division", "stage", "kind", "side_a", "side_b")
    graphs = []
    graph_info = model.get("graph_metadata", {})
    if isinstance(graph_info, list):
        graph_info = {item.get("file") or item.get("filename"): item for item in graph_info}
    for path in sorted(directory.glob("bracket-*.svg")):
        raw = path.read_bytes()
        root = ElementTree.fromstring(raw)
        graphs.append(
            {
                "title": root.get("aria-label", "對戰圖"),
                "division": graph_info.get(path.name, {}).get(
                    "division", root.get("data-division", "")
                ),
                "image": "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii"),
            }
        )
    data = {
        "name": model.get("public_name") or model.get("name", "壁球錦標賽"),
        "venue": model.get("venue", ""),
        "draft": model.get("draft", True),
        "evidence": bool(model.get("evidence")),
        "matches": [
            {
                key: str(row.get(key) or ("" if key in {"stage", "kind"} else "待確認"))
                for key in keys
            }
            for row in map(schedule_display, model.get("matches", []))
        ],
        "rules": [
            {
                **rule,
                "title": "共通比賽規則" if len(rule.get("scopes", [])) > 1 else rule["title"],
                "lines": (
                    ["適用：" + "、".join(rule["scopes"])]
                    if len(rule.get("scopes", [])) > 1
                    else []
                )
                + rule.get("lines", []),
            }
            for rule in model.get("rule_summaries", [])
        ],
        "graphs": graphs,
        "roster": [
            {
                "name": str(row["name"]),
                "division": str(row["division"]),
                "participant_id": str(row.get("participant_id") or ""),
            }
            for row in model.get("roster", [])
        ],
        "court_labels": [str(court["label"]) for court in model.get("courts", [])],
        "coverage_note": model.get("coverage_note"),
        "downloads": [
            name for name in ("booklet.pdf", "schedule.xlsx") if (directory / name).is_file()
        ],
    }
    payload = (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    path = directory / "index.html"
    links = "".join(
        f'<a class="download" href="{name}" download>{"下載 PDF" if name.endswith("pdf") else "下載 Excel"}</a>'
        for name in data["downloads"]
    )
    html = _HTML.replace(
        '<div class="downloads" id="downloads"></div>',
        '<div class="downloads" id="downloads">' + links + "</div>",
    ).replace("__DATA__", payload)
    font = files("squash_scheduler.assets").joinpath("fonts/IBMPlexSans.woff2").read_bytes()
    html = html.replace("__FONT__", base64.b64encode(font).decode("ascii"))
    path.write_text(html, "utf-8")
    return path


_HTML = files("squash_scheduler.assets").joinpath("portal.html").read_text("utf-8")
