"""Offline draft documents, printable match graphs and verifiable release bundles."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from jinja2 import Environment
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.pagebreak import Break
from pydantic import TypeAdapter

from ..domain.models import (
    EntrySide,
    MatchSide,
    Snapshot,
    canonical_hash,
    content_hash,
    new_id,
    utcnow,
)
from ..storage.repository import Repository, atomic_json
from .assets import font_payload, load_asset
from .messages import issue_label
from .prospectus import (
    PENDING,
    contains_demo_rule,
    prospectus_view,
    publication_content_issues,
    rule_summary,
)
from .schedule_rows import schedule_display

TEMPLATE_VERSION = "1.0.0"


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "title"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script", "title"}:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _controlled_font(model: dict, html: str, asset_root: Path | None) -> tuple[str, dict]:
    asset_id = model["publication"].get("font_asset_id")
    if not asset_id:
        if not model["draft"]:
            raise ValueError("Formal rendering requires an imported controlled font_asset_id")
        return "", {"mode": "system_preview", "family": "Microsoft JhengHei,sans-serif"}
    if asset_root is None:
        raise ValueError("Configured font assets require the controlled asset_root directory")
    font = load_asset(asset_root, asset_id, "font")
    text = _VisibleText()
    text.feed(html)
    embedded, metadata = font_payload(font, "".join(text.parts) + "0123456789 /")
    data_url = (
        "data:" + metadata["media_type"] + ";base64," + base64.b64encode(embedded).decode("ascii")
    )
    css = (
        "@font-face{font-family:'TournamentAsset';src:url('"
        + data_url
        + "') format('"
        + metadata["format"]
        + "');font-style:normal;font-weight:100 900;}"
        "body,.identity,svg,svg text{font-family:'TournamentAsset' !important;}"
    )
    return css, {"mode": "controlled", **metadata}


def side_label(snapshot: Snapshot, side: Any) -> str:
    if side.type == "entry":
        entry = next(e for e in snapshot.entries if e.id == side.entry_id)
        reg = next(r for r in snapshot.registrations if r.id == entry.registration_id)
        person = next(p for p in snapshot.participants if p.id == reg.participant_id)
        same = [p for p in snapshot.participants if p.display_name == person.display_name]
        return person.display_name + (f" [{person.id[:8]}]" if len(same) > 1 else "")
    if side.type == "pool_rank":
        pool = next(p for p in snapshot.pools if p.id == side.pool_id)
        return f"{pool.label} 池第 {side.rank} 名"
    if side.type in ("match_winner", "match_loser"):
        match = next(m for m in snapshot.matches if m.id == side.match_id)
        return f"{match.display_code or match.id[:8]} {'勝者' if side.type == 'match_winner' else '敗者'}"
    if side.type == "bye":
        return "輪空"
    return f"待抽籤位 {side.slot_id}（{side.draw_run_id[:8]}）"


def view_model(snapshot: Snapshot, *, draft: bool = True) -> dict[str, Any]:
    from ..domain.encouragement_draws import validate_encouragement

    errors = validate_encouragement(snapshot)
    if errors:
        raise ValueError("; ".join(i.message for i in errors))
    event = snapshot.config.get("event", {})
    tz = ZoneInfo(event.get("timezone") or "UTC")
    divisions = {d.id: d for d in snapshot.divisions}
    registrations = {reg.id: reg for reg in snapshot.registrations}
    participant_divisions: dict[str, list[str]] = {}
    for entry in snapshot.entries:
        person_id = registrations[entry.registration_id].participant_id
        name = divisions[entry.effective_division_id].name
        names = participant_divisions.setdefault(person_id, [])
        if name not in names:
            names.append(name)
    stages = {s.id: s for s in snapshot.stages}
    elimination_stages = {stage.id for stage in snapshot.stages if stage.type == "elimination"}
    final_rounds: dict[str, int] = {}
    for match in snapshot.matches:
        if match.kind == "competitive" and match.stage_id in elimination_stages:
            final_rounds[match.stage_id] = max(match.round, final_rounds.get(match.stage_id, 0))
    side_adapter: TypeAdapter[Any] = TypeAdapter(cast(Any, MatchSide))
    courts = {c["id"]: c for c in snapshot.config.get("courts", [])}
    assignments = {a.match_id: a for a in snapshot.assignments}
    matches = []
    for match in snapshot.matches:
        if match.status in ("cancelled", "excluded"):
            continue
        round_label = None
        if match.kind == "competitive" and match.stage_id in final_rounds:
            round_label = {0: "冠亞", 1: "四強", 2: "八強"}.get(
                final_rounds[match.stage_id] - match.round,
                "首輪" if match.round == 1 else f"第{match.round}輪",
            )
        assignment = assignments.get(match.id)
        local = assignment.start_at.astimezone(tz) if assignment else None
        matches.append(
            {
                "match_id": match.id,
                "code": match.display_code or match.id[:8],
                "division_id": match.division_id,
                "division": divisions[match.division_id].name,
                "stage_id": match.stage_id,
                "stage_type": stages[match.stage_id].type,
                "stage": getattr(stages[match.stage_id], "label", None)
                or {"round_robin": "循環賽", "elimination": "淘汰賽", "placement": "名次賽"}[
                    stages[match.stage_id].type
                ],
                "kind": "表演賽"
                if match.kind == "exhibition"
                else "季殿／名次賽"
                if match.kind == "placement"
                else "正式賽",
                "side_a": side_label(snapshot, match.side_a),
                "side_b": side_label(snapshot, match.side_b),
                "side_a_ref": match.side_a.model_dump(mode="json"),
                "side_b_ref": match.side_b.model_dump(mode="json"),
                "date": local.strftime("%Y-%m-%d") if local else "待排程",
                "time": local.strftime("%H:%M") if local else "待排程",
                "court": courts[assignment.court_id]["label"] if assignment else "待排程",
                "court_order": courts[assignment.court_id]["sort_order"] if assignment else 0,
                "round": match.round,
                **({"round_label": round_label} if round_label else {}),
                "position": match.bracket_position,
                "sources": [
                    s.match_id
                    for s in (match.side_a, match.side_b)
                    if s.type in ("match_winner", "match_loser")
                ],
            }
        )
    matches.sort(key=lambda m: (m["date"], m["time"], m["court_order"], m["code"]))
    rule_summaries = rule_summary(snapshot)
    roster_candidates = []
    sources = {s["id"]: s for s in snapshot.sources}
    if draft:
        for batch in snapshot.imports:
            if batch.get("status") == "superseded":
                continue
            for candidate in batch.get("candidates", []):
                if candidate.get("resolution") or candidate.get("status") == "ignored_with_reason":
                    continue
                ref = candidate["source_ref"]
                source = sources.get(ref["source_file_id"], {})
                source_name = source.get("original_name", ref["source_file_id"])
                if source.get("converted_from_source_id") in sources:
                    source_name = (
                        sources[source["converted_from_source_id"]]["original_name"] + "（轉檔）"
                    )
                position = (
                    f"段落 {ref['paragraph_index'] + 1}"
                    if "paragraph_index" in ref
                    else f"表格 {ref.get('table_index', 0) + 1}、列 {ref.get('row_index', 0) + 1}"
                )
                if "character_start" in ref:
                    position += f"、字元 {ref['character_start'] + 1}～{ref['character_end']}"
                roster_candidates.append(
                    {
                        "candidate_id": candidate["id"],
                        "batch_id": batch["id"],
                        "source_name": source_name,
                        "position": position,
                        "division": divisions[candidate["division_id"]].name
                        if candidate.get("division_id") in divisions
                        else "組別待確認",
                        "official_name": candidate["official_name"],
                        "status": "待人工確認身分與原始姓名",
                    }
                )
    return {
        "schema_version": "1.0.0",
        "revision_id": snapshot.id,
        "content_hash": content_hash(snapshot),
        "name": event.get("name") or "未命名賽事",
        "venue": event.get("venue_name") or "場館待設定",
        "timezone": event.get("timezone") or "未設定",
        "event_dates": event.get("event_dates") or [],
        "draft": draft,
        "matches": matches,
        "publication": snapshot.config.get("publication", {}),
        "issues": [i.model_dump(mode="json") for i in snapshot.issues if i.status == "open"],
        "issue_history": [i.model_dump(mode="json") for i in snapshot.issues],
        "changes": snapshot.changes,
        "rule_summaries": rule_summaries,
        "prospectus": prospectus_view(snapshot.config),
        "has_demo_rules": any(
            contains_demo_rule(match.rule_snapshot)
            for match in snapshot.matches
            if match.status not in {"cancelled", "excluded"}
        ),
        "divisions": [division.model_dump(mode="json") for division in snapshot.divisions],
        "deferrals": [
            {
                **draw,
                "slots": [
                    {
                        **slot,
                        "candidate_labels": [
                            side_label(snapshot, side_adapter.validate_python(side))
                            for side in slot.get("candidate_sides", [])
                        ],
                    }
                    for slot in draw.get("slots", [])
                ],
            }
            for draw in snapshot.draws
            if draw.get("kind") == "deferred_slots"
        ],
        "roster_candidates": roster_candidates,
        "courts": [
            {"id": court["id"], "label": court["label"], "sort_order": court["sort_order"]}
            for court in snapshot.config.get("courts", [])
        ],
        "roster": [
            {
                "name": next(
                    p.display_name
                    for p in snapshot.participants
                    if p.id == registrations[entry.registration_id].participant_id
                ),
                "division": divisions[entry.effective_division_id].name,
                "participant_id": registrations[entry.registration_id].participant_id,
                "registration_id": entry.registration_id,
                "entry_id": entry.id,
                "source_ref": registrations[entry.registration_id].source_ref,
                "status": entry.status,
            }
            for entry in snapshot.entries
        ],
        "stages": [stage.model_dump(mode="json") for stage in snapshot.stages],
        "pools": [
            {
                **pool.model_dump(mode="json"),
                "entries": [
                    {
                        "entry_id": entry_id,
                        "label": side_label(snapshot, EntrySide(entry_id=entry_id)),
                    }
                    for entry_id in pool.entry_ids
                ],
            }
            for pool in snapshot.pools
        ],
        "participants": [
            {
                "id": person.id,
                "display_name": person.display_name,
                "division_names": participant_divisions.get(person.id, []),
            }
            for person in snapshot.participants
        ],
    }


def _rule_text(value: Any) -> str:
    if value is None:
        return "尚未設定"
    if isinstance(value, bool):
        return "是" if value else "否"
    labels = {
        "include_in_rankings": "計入排名",
        "counts_as_played": "計入實際出賽",
        "winner_points": "勝方積分",
        "loser_points": "敗方積分",
        "side_a_points": "甲方積分",
        "side_b_points": "乙方積分",
        "walkover": "棄權",
        "retired": "受傷退賽",
        "double_forfeit": "雙方棄權",
        "manual_review": "人工賽務處理",
        "minutes": "分鐘",
        "action": "處置",
        "mode": "方式",
        "description": "說明",
        "note": "說明",
    }
    if isinstance(value, dict):
        return "；".join(f"{labels.get(k, k)}：{_rule_text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "、".join(_rule_text(v) for v in value)
    return labels.get(str(value), str(value))


def _wrap(text: str, width: int = 23) -> list[str]:
    lines, line, size = [], "", 0
    for char in text:
        weight = 2 if ord(char) > 255 else 1
        if size + weight > width:
            lines.append(line)
            line, size = "", 0
        line += char
        size += weight
    return lines + [line]


def bracket_svg(rows: list[dict], source_codes: dict[str, str] | None = None) -> str:
    """Graph nodes and reference edges; text wraps and never truncates names."""
    positions, elements = {}, []
    ordered = sorted(rows, key=lambda r: (r["round"], r["position"], r["code"]))
    round_columns = {
        round_number: index for index, round_number in enumerate(sorted({r["round"] for r in rows}))
    }
    included_ids = {row["match_id"] for row in rows}
    source_codes = source_codes or {row["match_id"]: row["code"] for row in rows}
    columns: dict[int, int] = {}
    max_bottom = 60
    for row in ordered:
        column = round_columns[row["round"]]
        y = columns.get(column, 15)
        x = column * 330 + 10
        lines = [
            row["code"] + " · " + row["kind"],
            *_wrap(row["side_a"]),
            "對",
            *_wrap(row["side_b"]),
        ]
        continued = [
            source_codes.get(source, source[:8])
            for source in row["sources"]
            if source not in included_ids
        ]
        if continued:
            lines.extend(_wrap("續前圖：" + " / ".join(continued)))
        height = 26 + len(lines) * 20
        positions[row["match_id"]] = (x, y, height)
        columns[column] = y + height + 28
        max_bottom = max(max_bottom, y + height + 20)
        elements.append(
            f'<rect x="{x}" y="{y}" width="300" height="{height}" rx="5" fill="white" stroke="#355e55"/>'
        )
        for i, line in enumerate(lines):
            elements.append(
                f'<text x="{x + 12}" y="{y + 24 + i * 20}" font-size="16">{escape(line)}</text>'
            )
    edges = []
    for row in ordered:
        x, y, h = positions[row["match_id"]]
        for source in row["sources"]:
            if source in positions:
                sx, sy, sh = positions[source]
                edges.append(
                    f'<path d="M {sx + 300},{sy + sh / 2} H {x - 15} V {y + h / 2} H {x}" stroke="#355e55" fill="none"/>'
                )
    width = max((v[0] + 320 for v in positions.values()), default=340)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="對戰圖" '
        f'viewBox="0 0 {width} {max_bottom}" width="{width}" height="{max_bottom}" '
        f'style="font-family:Microsoft JhengHei,sans-serif">' + "".join(edges + elements) + "</svg>"
    )


def export_excel(snapshot: Snapshot, model: dict, path: Path) -> None:
    from ..domain.encouragement_draws import validate_encouragement

    errors = validate_encouragement(snapshot)
    if errors:
        raise ValueError("; ".join(i.message for i in errors))
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name: str, rows: list[dict]) -> None:
        ws = wb.create_sheet(name)
        keys = list(dict.fromkeys(k for row in rows for k in row)) or ["status"]
        for line, row in enumerate([dict(zip(keys, keys)), *rows], 1):
            for col, key in enumerate(keys, 1):
                value = row.get(key)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                cell = ws.cell(line, col, value)
                # Explicit string type prevents Excel interpreting untrusted text as formulas.
                if isinstance(value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if line == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="244F47")
            ws.row_dimensions[line].height = 45 if line > 1 else 28
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = 24
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        ws.print_title_rows = "1:1"
        publication = model["publication"]
        ws.page_setup.orientation = publication.get("orientation") or "landscape"
        ws.page_setup.paperSize = (
            ws.PAPERSIZE_A3 if publication.get("paper_size") == "A3" else ws.PAPERSIZE_A4
        )
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    sheet(
        "Metadata",
        [
            {
                "revision_id": model["revision_id"],
                "content_hash": model["content_hash"],
                "status": "DRAFT" if model["draft"] else "APPROVED",
                "name": model["name"],
            }
        ],
    )
    for name, collection in (
        ("Participants", snapshot.participants),
        ("Registrations", snapshot.registrations),
        ("Divisions", snapshot.divisions),
    ):
        sheet(name, [item.model_dump(mode="json") for item in collection])
    sheet("Matches", model["matches"])
    sheet(
        "Schedule",
        [
            {
                k: row[k]
                for k in (
                    "match_id",
                    "code",
                    "date",
                    "time",
                    "court",
                    "division",
                    "stage",
                    "kind",
                    "side_a",
                    "side_b",
                )
            }
            for row in map(schedule_display, model["matches"])
        ],
    )
    sheet("Issues", model.get("issue_history", model["issues"]))
    sheet("Changes", model["changes"])
    if model.get("roster_candidates"):
        sheet("ImportCandidates", model["roster_candidates"])
    for index, division in enumerate(snapshot.divisions, 1):
        rows = [row for row in model["matches"] if row["division_id"] == division.id]
        sheet(
            f"Bracket-{index}",
            [
                {k: row[k] for k in ("code", "stage", "round", "side_a", "side_b", "sources")}
                for row in rows
            ],
        )
        ws = wb.worksheets[-1]
        for row_index in range(20, len(rows) + 1, 19):
            ws.row_breaks.append(Break(id=row_index))
    wb.save(path)


_TEMPLATE = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>{{ name }} · {{ 'DRAFT' if draft else '核准版本' }}</title><style>
@page {size:{{ publication.paper_size or 'A4' }} {{ publication.orientation or 'landscape' }};margin:14mm} *{box-sizing:border-box}
body{font:11pt 'Microsoft JhengHei',sans-serif;color:#193b34;margin:0}
h1{font-size:24pt;margin:0 0 8mm}h2{font-size:17pt}p{line-height:1.7}
.stamp{font-size:14pt;color:#a03921;font-weight:bold}.identity{font:9pt monospace;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:9pt;table-layout:fixed}th,td{border:1px solid #9bada6;padding:7px;overflow-wrap:anywhere;vertical-align:top}
th{background:#e2ece7}thead{display:table-header-group}tr{break-inside:avoid}section{break-before:page}
.graph{break-inside:avoid}.graph svg{max-width:100%;height:auto;max-height:{{ graph_height_mm }}mm}
.qr{width:42mm;height:42mm}.error{color:#a03921}a{color:inherit}
img.sponsor-asset{max-width:120mm;max-height:70mm;object-fit:contain}
{{ font_css | safe }}
</style><h1>{{ name }}</h1>
{% if draft %}<p class="stamp">DRAFT 草案 · 合成原型或尚未核准 · 不可作正式公告</p>{% endif %}
<p>{{ venue }} · 時區：{{ timezone }}</p><p class="identity">Revision {{ revision_id }}<br>Content {{ content_hash }}</p>
{% if issues %}<h2>待處理事項</h2><ul>{% for i in issues %}<li>{{ issue_label(i.code) }} <small>（{{ i.code }} · {{ i.entity_id or '' }}）</small></li>{% endfor %}</ul>{% endif %}
{% if roster_candidates %}<section><h2>名單暫存審核 · DRAFT</h2><p>候選姓名保留來源空白；尚未建立有效報名。同名與跨組身分需要人工確認。</p><table><thead><tr><th>組別</th><th>原始候選姓名</th><th>來源位置</th><th>處理狀態</th><th>候選識別碼</th></tr></thead><tbody>{% for c in roster_candidates %}<tr><td>{{ c.division }}</td><td style="white-space:pre-wrap">{{ c.official_name }}</td><td>{{ c.source_name }} · {{ c.position }}</td><td>{{ c.status }}</td><td>{{ c.candidate_id }}</td></tr>{% endfor %}</tbody></table></section>{% endif %}
{% for rule in rule_summaries %}<section><h2>{{ rule.title }} · 比賽與排名規則</h2>{% for line in rule.lines %}<p>{{ line }}</p>{% endfor %}</section>{% endfor %}
{% for graph in graphs %}<section><h2>{{ graph.title }} · 對戰圖{% if draft %} · DRAFT{% endif %}</h2><div class="graph">{{ graph.svg | safe }}</div></section>{% endfor %}
<section><h2>預計賽程{% if draft %} · DRAFT{% endif %}</h2><table><thead><tr><th>場次</th><th>日期／時間</th><th>球場</th><th>組別／階段</th><th>對戰雙方</th></tr></thead><tbody>
{% for m in matches %}<tr><td>[{{ m.code }}]</td><td>{{ m.date }} {{ m.time }}</td><td>{{ m.court }}</td><td>{{ m.division }} · {{ m.stage }} · {{ m.kind }}</td><td>{{ m.side_a }} 對 {{ m.side_b }}</td></tr>{% endfor %}</tbody></table></section>
{% if qr_data %}<section><h2>賽事通知</h2><p>{{ publication.qr_label }}</p><img class="qr" src="{{ qr_data }}" alt="通知網址 QR Code"><p>{{ publication.qr_url }}</p></section>{% endif %}
{% if publication.sponsor_mode == 'provided' %}<section><h2>贊助資訊</h2>{% for sponsor in sponsors %}<h3>{{ sponsor.display_name }}</h3><p>{{ sponsor.text }}</p>{% if sponsor.image_data %}<img class="sponsor-asset" src="{{ sponsor.image_data }}" alt="{{ sponsor.display_name }}">{% endif %}{% endfor %}</section>{% endif %}
</html>"""


def render_html(model: dict, directory: Path, *, asset_root: Path | None = None) -> str:
    graphs = []
    publication = model["publication"]
    if not model["draft"]:
        if model.get("unlabelled_edges") or any(
            "source_schedule_sides" in row for row in model["matches"]
        ):
            raise ValueError(
                "Source-only historical evidence cannot become a formal event publication"
            )
        missing = publication_content_issues({"publication": publication})
        if missing:
            raise ValueError("Formal prospectus content is incomplete: " + ", ".join(missing))
        if model.get("has_demo_rules") or any(
            rule.get("is_demo") for rule in model.get("rule_summaries", [])
        ):
            raise ValueError("Formal rendering cannot publish demonstration match rules")
        if (
            publication.get("document_profile") == "prospectus"
            or publication.get("layout_template_id") == "reference-v2"
        ) and any(
            PENDING in line for rule in model.get("rule_summaries", []) for line in rule["lines"]
        ):
            raise ValueError("Formal booklet contains unresolved public match rules")
    template_id = publication.get("layout_template_id")
    reference = template_id in {"reference-v1", "reference-v2"}
    classic = template_id == "reference-v2"
    if template_id not in {None, "draft-v1", "standard-v1", "reference-v1", "reference-v2"}:
        raise ValueError("Unknown publication layout_template_id")
    if not model["draft"] and template_id not in {"standard-v1", "reference-v1", "reference-v2"}:
        raise ValueError("Formal rendering requires layout_template_id standard-v1 or reference-v1")
    paper = publication.get("paper_size") or "A4"
    orientation = publication.get("orientation") or "landscape"
    if paper not in {"A4", "A3"} or orientation not in {"landscape", "portrait"}:
        raise ValueError("Unsupported paper size or orientation")
    if reference and paper != "A4":
        raise ValueError("reference-v1 uses A4 landscape graphs and A4 portrait schedules")
    page_width, page_height = (297, 210) if paper == "A4" else (420, 297)
    if orientation == "portrait":
        page_width, page_height = page_height, page_width
    graph_width = (page_width - 28) * 96 / 25.4 - 10
    graph_height_mm = page_height - 58
    graph_height = graph_height_mm * 96 / 25.4
    stage_ids = list(dict.fromkeys(m["stage_id"] for m in model["matches"]))
    if reference:
        from .reference_layout import graph_pages

        graphs = graph_pages(model, directory, classic=classic)
        stage_ids = []
    for index, stage_id in enumerate(stage_ids, 1):
        rows = [m for m in model["matches"] if m["stage_id"] == stage_id]
        # 1000x560 CSS px fits A4 landscape inside margins and heading at 96 dpi.
        # Actual 16px graph text remains >=12px (9pt), including long references.
        import xml.etree.ElementTree as ET

        source_codes = {row["match_id"]: row["code"] for row in rows}
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for row in sorted(rows, key=lambda r: (r["round"], r["position"], r["code"])):
            candidate = current + [row]
            svg_root = ET.fromstring(bracket_svg(candidate, source_codes))
            width, height = float(svg_root.attrib["width"]), float(svg_root.attrib["height"])
            if current and (width > graph_width or height > graph_height):
                chunks.append(current)
                current = [row]
            else:
                current = candidate
            single = ET.fromstring(bracket_svg(current, source_codes))
            if (
                min(
                    graph_width / float(single.attrib["width"]),
                    graph_height / float(single.attrib["height"]),
                    1,
                )
                < 0.75
            ):
                raise ValueError(
                    "PDF_LAYOUT_OVERFLOW: a single graph node cannot fit at the minimum 9pt; review the reference layout"
                )
        if current:
            chunks.append(current)
        for part, chunk in enumerate(chunks, 1):
            svg = bracket_svg(chunk, source_codes)
            title = rows[0]["division"] + " · " + rows[0]["stage"] + f" · {part}/{len(chunks)}"
            svg = svg.replace(
                'aria-label="對戰圖"',
                'aria-label="'
                + escape(title, quote=True)
                + '" data-division="'
                + escape(rows[0]["division"], quote=True)
                + '"',
            )
            (directory / f"bracket-{index}-{part}.svg").write_text(svg, "utf-8")
            graphs.append(
                {
                    "title": title,
                    "svg": svg,
                }
            )
    qr_data = None
    pub = model["publication"]
    if pub.get("qr_mode") == "enabled":
        if not str(pub.get("qr_url", "")).startswith("https://"):
            raise ValueError("QR URL must use HTTPS")
        import qrcode

        image = qrcode.make(pub["qr_url"], border=4)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        qr_data = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    env = Environment(autoescape=True)
    sponsors, images = [], []
    if publication.get("sponsor_mode") == "provided":
        for sponsor in publication.get("sponsors", []):
            prepared = {**sponsor, "image_data": None}
            if sponsor.get("asset_id"):
                if asset_root is None:
                    raise ValueError("Sponsor image assets require a controlled asset_root")
                asset = load_asset(asset_root, sponsor["asset_id"], "image")
                prepared["image_data"] = (
                    "data:"
                    + asset["media_type"]
                    + ";base64,"
                    + base64.b64encode(asset["bytes"]).decode("ascii")
                )
                images.append({k: v for k, v in asset.items() if k != "bytes"})
            sponsors.append(prepared)
    template = env.from_string(_TEMPLATE)
    context = {
        **model,
        "issue_label": issue_label,
        "issues": [i for i in model.get("issues", []) if i.get("status", "open") == "open"],
        "graphs": graphs,
        "qr_data": qr_data,
        "graph_height_mm": graph_height_mm,
        "sponsors": sponsors,
    }
    if reference:
        from .reference_layout import VERSION, render, schedule_pages

        context["schedule_pages"] = schedule_pages(model, classic=classic)
        context["classic"] = classic
        context["front_matter"] = template_id == "reference-v2"
        context["unplayed_divisions"] = [
            {
                "name": division["name"],
                "names": [
                    row["name"]
                    for row in model.get("roster", [])
                    if row["division"] == division["name"]
                ],
            }
            for division in model.get("divisions", [])
            if not any(row["division_id"] == division["id"] for row in model["matches"])
        ]
        context["prospectus"] = model.get("prospectus") or prospectus_view(
            {"publication": publication}
        )
        model["graph_metadata"] = {
            graph["file"]: {
                **{key: value for key, value in graph.items() if key != "svg"},
                "division": next(
                    (
                        d["name"]
                        for d in model.get("divisions", [])
                        if d["id"] == graph["division_id"]
                    ),
                    "",
                ),
            }
            for graph in graphs
        }
        preliminary = render(context)
    else:
        preliminary = template.render(**context, font_css="")
    font_css, font_metadata = _controlled_font(model, preliminary, asset_root)
    model["render_assets"] = {"font": font_metadata, "images": images}
    model["template"] = {
        "id": template_id or "draft-v1",
        "version": VERSION if reference else TEMPLATE_VERSION,
    }
    if reference:
        return render(context, font_css=font_css)
    return template.render(**context, font_css=font_css)


def render_pdf(html: str, path: Path, *, html_path: Path | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # Installed Edge is usable offline on Windows; other systems use installed Chromium.
        system_edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        browser = pw.chromium.launch(
            headless=True, executable_path=str(system_edge) if system_edge.exists() else None
        )
        try:
            page = browser.new_page()
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if not route.request.url.startswith("data:")
                    else route.continue_()
                ),
            )
            page.set_content(html, wait_until="load")
            page.evaluate("document.fonts.ready")
            page.evaluate("""async () => {
                await Promise.all([...document.images].map(image => image.decode()));
                const controlled = [...document.fonts].filter(face => face.family.replaceAll("'", "").replaceAll('"', '') === 'TournamentAsset');
                if (controlled.some(face => face.status !== 'loaded')) throw new Error('Controlled font failed to load');
            }""")
            page.emulate_media(media="print")
            overflows = page.evaluate(
                """() => {
                    const cells = [...document.querySelectorAll('td')].filter(e => e.scrollWidth > e.clientWidth+2 && e.clientWidth>0);
                    const graph = [...document.querySelectorAll('svg text')].filter(e => {
                        const bounds = e.getBBox(), x = Number(e.getAttribute('x')), y = Number(e.getAttribute('y'));
                        const view = e.ownerSVGElement.viewBox.baseVal;
                        if (view.width && (bounds.x < view.x || bounds.y < view.y || bounds.x + bounds.width > view.x + view.width || bounds.y + bounds.height > view.y + view.height)) return true;
                        const node = [...e.ownerSVGElement.querySelectorAll('rect')].find(r => {
                            const a = r.getBBox(); return x >= a.x && x <= a.x+a.width && y >= a.y && y <= a.y+a.height;
                        });
                        if (!node) return false;
                        const box = node.getBBox();
                        const padding = Number(node.getAttribute('data-text-padding') ?? 5);
                        return bounds.x < box.x || bounds.x + bounds.width > box.x + box.width - padding;
                    });
                    return [...cells, ...graph].slice(0, 8).map(e => ({
                        text: e.textContent.slice(0, 100),
                        graph: e.closest('svg')?.getAttribute('aria-label') || null,
                        width: e.clientWidth, scrollWidth: e.scrollWidth
                    }));
                }"""
            )
            if overflows:
                raise ValueError(
                    "PDF_LAYOUT_OVERFLOW: visible text exceeds its container: "
                    + json.dumps(overflows, ensure_ascii=False)
                )

            def print_document() -> None:
                page.pdf(
                    path=str(path),
                    prefer_css_page_size=True,
                    print_background=True,
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template='<div style="font-size:9px;width:100%;text-align:center"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                )

            print_document()
            navigation_summary = {"enabled": False, "entries": 0}
            if page.locator("[data-toc-target]").count():
                from .pdf_navigation import (
                    add_pdf_bookmarks,
                    extract_navigation,
                    fill_toc_pages,
                    validate_navigation,
                )

                first_pass = extract_navigation(path, html)
                resolved_html = fill_toc_pages(html, first_pass)
                page.set_content(resolved_html, wait_until="load")
                page.evaluate("""async () => {
                    await document.fonts.ready;
                    await Promise.all([...document.images].map(image => image.decode()));
                }""")
                print_document()
                navigation = validate_navigation(path, resolved_html, first_pass)
                add_pdf_bookmarks(path, navigation)
                validate_navigation(path, resolved_html, navigation)
                atomic_json(path.parent / "navigation-map.json", navigation)
                if html_path is not None:
                    html_path.write_text(resolved_html, "utf-8")
                navigation_summary = {"enabled": True, "entries": len(navigation["entries"])}
            return {
                "renderer": "playwright",
                "browser_version": browser.version,
                "navigation": navigation_summary,
            }
        finally:
            browser.close()


def _require_pdf_body_pages(document: Any) -> None:
    """Reject overflow pages consisting only of a draft stamp or page counter."""
    for index, page in enumerate(document, 1):
        body = [
            line.strip()
            for line in page.get_text().splitlines()
            if line.strip()
            and not re.fullmatch(r"\d+\s*/\s*\d+", line.strip())
            and not line.strip().startswith(("審閱草案", "DRAFT 草案", "DRAFT "))
        ]
        if not body and not page.get_images():
            raise ValueError(
                f"PDF_LAYOUT_OVERFLOW: page {index} has no body content beyond its draft stamp or page number"
            )


def document_qa(model: dict, directory: Path) -> dict:
    import pymupdf
    import zxingcpp
    from PIL import Image

    graph_coverage = None
    reference = model["publication"].get("layout_template_id") in {"reference-v1", "reference-v2"}
    classic = model["publication"].get("layout_template_id") == "reference-v2"
    if reference:
        from collections import Counter

        actual_ids = Counter(
            match_id
            for graph in model.get("graph_metadata", {}).values()
            for match_id in graph.get("match_ids", [])
        )
        expected_ids = Counter({row["match_id"]: 1 for row in model["matches"]})
        if actual_ids != expected_ids:
            raise ValueError("RELEASE_ARTIFACT_MISMATCH: graph coverage omits or repeats matches")
        graph_coverage = {"passed": True, "match_count": len(expected_ids)}

    navigation_qa = None
    if model["publication"].get("layout_template_id") == "reference-v2":
        from .pdf_navigation import validate_navigation

        navigation_file = directory / "navigation-map.json"
        html_file = directory / "review.html"
        if not navigation_file.is_file() or not html_file.is_file():
            raise ValueError("RELEASE_ARTIFACT_MISMATCH: booklet navigation evidence is missing")
        navigation = json.loads(navigation_file.read_text("utf-8"))
        validate_navigation(directory / "booklet.pdf", html_file.read_text("utf-8"), navigation)
        with pymupdf.open(directory / "booklet.pdf") as navigation_pdf:
            expected_outline = [
                [entry["level"], entry["title"], entry["page"]] for entry in navigation["entries"]
            ]
            if navigation_pdf.get_toc() != expected_outline:
                raise ValueError(
                    "RELEASE_ARTIFACT_MISMATCH: booklet bookmarks differ from contents"
                )
        navigation_qa = {"passed": True, "entry_count": len(navigation["entries"])}

    wb = load_workbook(directory / "schedule.xlsx", data_only=False)
    headers = [c.value for c in wb["Schedule"][1]]
    xlsx_rows = [
        dict(zip(headers, row)) for row in wb["Schedule"].iter_rows(min_row=2, values_only=True)
    ]
    expected = [{k: row[k] for k in headers} for row in map(schedule_display, model["matches"])]
    if xlsx_rows != expected:
        raise ValueError("RELEASE_ARTIFACT_MISMATCH: spreadsheet rows differ from view model")
    if any(cell.data_type == "f" for ws in wb for row in ws for cell in row):
        raise ValueError("RELEASE_ARTIFACT_MISMATCH: unexpected spreadsheet formula")
    # Opening bytes also guarantees Windows can remove a failed pending bundle
    # when validation raises before the PDF document is explicitly closed.
    doc = pymupdf.open(stream=(directory / "booklet.pdf").read_bytes(), filetype="pdf")
    _require_pdf_body_pages(doc)
    text = "".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    normalized = "".join(text.split())
    if reference:
        from .reference_layout import (
            CLASSIC_SCHEDULE_WIDTHS,
            MARGIN_MM,
            SCHEDULE_WIDTHS,
            date_label,
            print_text,
            schedule_cells,
        )
    for summary in model.get("rule_summaries", []):
        for rule_line in summary["lines"]:
            if "".join(rule_line.split()) not in normalized:
                raise ValueError("RELEASE_ARTIFACT_MISMATCH: PDF omits an effective match rule")
    if not classic:
        for row in map(schedule_display, model["matches"]):
            for key in ("code", "date", "time", "court", "division", "side_a", "side_b"):
                value = print_text(str(row[key])) if reference else str(row[key])
                if "".join(value.split()) not in normalized:
                    raise ValueError(
                        f"RELEASE_ARTIFACT_MISMATCH: PDF omits {row['match_id']} {key}"
                    )
    if "\ufffd" in text or "\x00" in text:
        raise ValueError("PDF_LAYOUT_OVERFLOW: PDF contains a missing-glyph replacement")
    # Verify visible schedule cells per row. A token appearing elsewhere on a page
    # cannot establish that the correct time/court/opponents belong to this match.
    expected_rows = {
        row["code"] if classic else f"[{row['code']}]": row
        for row in map(schedule_display, model["matches"])
    }
    if len(expected_rows) != len(model["matches"]):
        raise ValueError("RELEASE_ARTIFACT_MISMATCH: duplicate display codes")
    found_rows: set[str] = set()
    margin = (MARGIN_MM if reference else 14) * 72 / 25.4

    def normalize(value: Any) -> str:
        return "".join(str(value).split())

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        widths = (
            CLASSIC_SCHEDULE_WIDTHS if classic else SCHEDULE_WIDTHS if reference else (0.2,) * 5
        )
        boundaries = [margin]
        for width in widths:
            boundaries.append(boundaries[-1] + (page.rect.width - 2 * margin) * width)
        anchors: list[tuple[Any, str]] = []
        if classic:
            headers = [box for box in page.search_for("場次") if margin <= box.x0 < boundaries[1]]
            time_headers = page.search_for("預計時間")
            header = next(
                (
                    box
                    for box in headers
                    if any(
                        abs(time_box.y0 - box.y0) < 2
                        and boundaries[1] <= time_box.x0 < boundaries[2]
                        for time_box in time_headers
                    )
                ),
                None,
            )
            if header is None:
                continue
            code_column = pymupdf.Rect(margin, header.y1, boundaries[1], page.rect.height - margin)
            for block in page.get_text("dict", clip=code_column)["blocks"]:
                for line in block.get("lines", []):
                    marker = "".join(span["text"] for span in line["spans"]).strip()
                    if marker not in expected_rows:
                        raise ValueError(
                            "RELEASE_ARTIFACT_MISMATCH: unexpected schedule match code"
                        )
                    anchors.append((pymupdf.Rect(line["bbox"]), marker))
        else:
            for marker in expected_rows:
                anchors.extend(
                    (bbox, marker)
                    for bbox in page.search_for(marker)
                    if margin <= bbox.x0 < boundaries[1]
                )
        anchors.sort(key=lambda item: item[0].y0)
        page_text = normalize(page.get_text()) if classic and anchors else ""
        for index, (bbox, marker) in enumerate(anchors):
            if marker in found_rows:
                raise ValueError("RELEASE_ARTIFACT_MISMATCH: schedule row appears more than once")
            bottom = (
                anchors[index + 1][0].y0 - 1
                if index + 1 < len(anchors)
                else page.rect.height - margin
            )
            row = expected_rows[marker]
            if classic and normalize(date_label(row["date"])) not in page_text:
                raise ValueError(
                    "RELEASE_ARTIFACT_MISMATCH: schedule row appears under the wrong date"
                )
            expected_cells = [
                marker,
                f"{row['date']} {row['time']}",
                row["court"],
                f"{row['division']} · {row['stage']} · {row['kind']}",
                f"{row['side_a']} 對 {row['side_b']}",
            ]
            if reference:
                expected_cells = schedule_cells(row, classic=classic)
            for column, expected_cell in enumerate(expected_cells):
                clip = pymupdf.Rect(
                    boundaries[column],
                    bbox.y0 - (3 if classic else 1),
                    boundaries[column + 1],
                    bottom,
                )
                actual = page.get_text("text", clip=clip, sort=True)
                if normalize(actual) != normalize(expected_cell):
                    raise ValueError(
                        f"RELEASE_ARTIFACT_MISMATCH: PDF schedule row {row['match_id']} column {column + 1} differs"
                    )
            found_rows.add(marker)
    if found_rows != set(expected_rows):
        raise ValueError("RELEASE_ARTIFACT_MISMATCH: PDF omits visible schedule row markers")
    qr_values: list[str] = []
    pages = []
    for index in range(1, doc.page_count + 1):
        page = doc.load_page(index - 1)
        if not page.get_text().strip():
            raise ValueError("PDF_LAYOUT_OVERFLOW: empty page")
        has_body_text = False
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["bbox"][1] < page.rect.height - 25 and span["text"].strip():
                        has_body_text = True
                    if (
                        span["bbox"][1] < page.rect.height - 25
                        and span["text"].strip()
                        and span["size"] < 8.9
                    ):
                        raise ValueError(
                            "PDF_LAYOUT_OVERFLOW: body text is smaller than the minimum 9pt"
                        )
        if not has_body_text:
            raise ValueError("PDF_LAYOUT_OVERFLOW: page contains only a footer")
        for block in page.get_text("blocks"):
            if (
                block[0] < 0
                or block[1] < 0
                or block[2] > page.rect.width + 1
                or block[3] > page.rect.height + 1
            ):
                raise ValueError("PDF_LAYOUT_OVERFLOW: text outside page bounds")
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
        png = directory / f"page-{index:03}.png"
        pix.save(str(png))
        pixels = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        qr_values.extend(result.text for result in zxingcpp.read_barcodes(pixels))
        pages.append(
            {
                "page": index,
                "width": page.rect.width,
                "height": page.rect.height,
                "text_blocks": len(page.get_text("blocks")),
                "preview": png.name,
            }
        )
    if (
        model["publication"].get("qr_mode") == "enabled"
        and model["publication"]["qr_url"] not in qr_values
    ):
        raise ValueError("RELEASE_ARTIFACT_MISMATCH: rendered PDF QR cannot be decoded")
    doc.close()
    return {
        "passed": True,
        "pages": pages,
        "qr_values": qr_values,
        "match_count": len(expected),
        "graph_coverage": graph_coverage,
        "navigation": navigation_qa,
        "visual_review": "required",
        "historical_layout_verified": False,
    }


def build_documents(
    snapshot: Snapshot, destination: Path, *, draft: bool = True, asset_root: Path | None = None
) -> dict:
    """A failed bundle stays failed; latest-ready is updated only by release()."""
    from ..domain.encouragement_draws import validate_encouragement
    from ..domain.formats import pending_format_divisions

    if pending_format_divisions(snapshot):
        raise ValueError("FORMAT_GRAPH_PENDING: complete approved draws before exporting")
    errors = validate_encouragement(snapshot)
    if errors:
        raise ValueError("; ".join(i.message for i in errors))
    destination.mkdir(parents=True, exist_ok=False)
    model = view_model(snapshot, draft=draft)
    html = render_html(model, destination, asset_root=asset_root)
    atomic_json(destination / "render-model.json", model)
    (destination / "review.html").write_text(html, "utf-8")
    export_excel(snapshot, model, destination / "schedule.xlsx")
    renderer = (
        render_pdf(html, destination / "booklet.pdf", html_path=destination / "review.html")
        if model["publication"].get("layout_template_id") == "reference-v2"
        else render_pdf(html, destination / "booklet.pdf")
    )
    qa = document_qa(model, destination)
    atomic_json(destination / "qa-report.json", qa)
    from .portal import write_portal

    write_portal(model, destination)
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in destination.iterdir()
        if p.is_file()
    }
    manifest = {
        "schema_version": "1.0.0",
        "revision_id": snapshot.id,
        "content_hash": content_hash(snapshot),
        "render_model_hash": canonical_hash(model),
        "artifact_hashes": hashes,
        "build_versions": {
            **renderer,
            "template_id": model["template"]["id"],
            "template_version": model["template"]["version"],
        },
        "render_assets": model["render_assets"],
        "status": "draft" if draft else "qa_pending",
        "created_at": utcnow().isoformat(),
        "qa": qa,
    }
    atomic_json(destination / "manifest.json", manifest)
    return manifest


def release(repo: Repository, revision_id: str) -> dict:
    """Formal ready requires both validation and a reviewed output layout."""
    from ..domain.validation import validate

    snapshot = repo.load(revision_id)
    if snapshot.status != "approved" or not validate(snapshot, "G5")["passed"]:
        raise ValueError("Formal release requires an approved G5 snapshot")
    # This first implementation deliberately cannot label unreviewed typography as ready.
    # The generated QA bundle gives the operator every rendered page to review.
    release_id = new_id()
    releases = repo.root / "releases"
    releases.mkdir(exist_ok=True)
    pending = releases / (".pending-" + release_id)
    try:
        manifest = build_documents(snapshot, pending, draft=False, asset_root=repo.root / "assets")
        manifest.update(release_id=release_id, status="qa_pending")
        atomic_json(pending / "manifest.json", manifest)
        final = releases / release_id
        pending.rename(final)
        return {
            "release_id": release_id,
            "status": "qa_pending",
            "path": str(final),
            "manifest_hash": canonical_hash(manifest),
            "required_pages": [page["page"] for page in manifest["qa"]["pages"]],
            "message": "All rendered pages require organizer visual review before release readiness",
        }
    except Exception as exc:
        if pending.exists():
            shutil.rmtree(pending)
        failed = {
            "release_id": release_id,
            "revision_id": revision_id,
            "status": "failed",
            "error": str(exc),
        }
        atomic_json(releases / f"{release_id}-failed.json", failed)
        raise
