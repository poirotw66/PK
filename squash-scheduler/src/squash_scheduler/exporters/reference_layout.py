"""Black and white tournament sheets following the supplied Chung Cheng Cup reference.

Graphs are generated from typed match sides, never from participant name matching.
The source PDF supplies the visual vocabulary, not draw assignments or court data.
"""

from __future__ import annotations

import math
import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment

from .schedule_rows import schedule_display

VERSION = "2.3.1"
MARGIN_MM = 12
SCHEDULE_WIDTHS = (0.14, 0.10, 0.10, 0.18, 0.24, 0.24)
CLASSIC_SCHEDULE_WIDTHS = (0.11, 0.18, 0.17, 0.27, 0.27)
KAI_FONTS = "'DFKai-SB','BiauKaiTC','BiauKai','TournamentAsset',serif"


def date_label(value: str) -> str:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{day.year}年{day.month}月{day.day}日　星期{'一二三四五六日'[day.weekday()]}"


def schedule_cells(row: dict, *, classic: bool = False) -> list[str]:
    row = schedule_display(row)
    stage = {"循環賽": "循環", "淘汰賽": "決賽", "名次賽": "名次賽"}.get(row["stage"], row["stage"])
    if row["kind"] == "季殿／名次賽" and row.get("stage_type") != "placement":
        stage = "季殿／名次賽"
    elif row["kind"] == "表演賽":
        stage = "表演賽"
    if classic:
        division = row["division"]
        for school in ("高中", "國中", "國小"):
            if division in {f"{school}男子組", f"{school}女子組"}:
                division = school + division[2]
                break
        hour, _, minute = row["time"].partition(":")
        time = f"{int(hour)}點　{minute}分" if hour.isdigit() and minute.isdigit() else row["time"]
        if row.get("round_label"):
            stage = row["round_label"]
        elif stage == "循環":
            stage = ""
        if row["kind"] == "季殿／名次賽" and row.get("stage_type") != "placement":
            stage = "季殿"
        return [
            row["code"],
            time,
            division + stage,
            print_text(row["side_a"]),
            print_text(row["side_b"]),
        ]
    return [
        f"[{row['code']}]",
        row["time"],
        row["court"],
        row["division"] + ("　" + stage if stage else ""),
        print_text(row["side_a"]),
        print_text(row["side_b"]),
    ]


def print_text(value: str) -> str:
    """Kai lacks circled glyphs; keep their meaning without missing characters."""
    return "".join(
        f"圈{ord(char) - ord('①') + 1}" if "①" <= char <= "⑳" else char for char in value
    )


def _wrap(value: str, limit: int = 22) -> list[str]:
    lines, current, length = [], "", 0
    for char in value:
        size = 2 if ord(char) > 255 else 1
        if current and length + size > limit:
            lines.append(current)
            current, length = "", 0
        current += char
        length += size
    return [*lines, current]


def _short_cjk_name(label: str) -> bool:
    return 2 <= len(label) <= 3 and all("\u3400" <= char <= "\u9fff" for char in label)


def _text(
    x: float, y: float, value: Any, size: int = 18, anchor: str = "middle", limit: int = 22
) -> str:
    if len(str(value)) == 1 and "①" <= str(value) <= "⑳":
        digit = str(ord(str(value)) - ord("①") + 1)
        return (
            f'<circle cx="{x:.2f}" cy="{y - size * 0.35:.2f}" r="{size * 0.65:.2f}" '
            'fill="none" stroke="black" stroke-width="1.2"/>' + _text(x, y, digit, size, anchor)
        )
    lines = _wrap(str(value), limit)
    if len(lines) > 3:
        raise ValueError(
            "PDF_LAYOUT_OVERFLOW: reference graph label requires more than three lines"
        )
    spans = "".join(
        f'<tspan x="{x:.2f}" dy="{0 if i == 0 else size + 3}">{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    return f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}">{spans}</text>'


def _line(x1: float, y1: float, x2: float, y2: float) -> str:
    return f'<path d="M{x1:.2f},{y1:.2f} L{x2:.2f},{y2:.2f}"/>'


def _svg(elements: list[str], title: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 530" '
        'width="1000" height="530" role="img" '
        f'aria-label="{escape(title, quote=True)}">'
        "<style>path{fill:none;stroke:#000;stroke-width:2.2}text{fill:#000;"
        "font-family:DFKai-SB,BiauKai,serif}rect{fill:white;stroke:black}</style>"
        + "".join(elements)
        + "</svg>"
    )


def _round_robin_records(
    pools: list[dict], matches: list[dict], title: str, *, classic: bool = False
) -> str:
    """Legible scoring matrix and numbered pair list when edge labels cannot fit."""
    elements = []
    panel = 1000 / len(pools)
    for index, pool in enumerate(pools):
        entries = pool["entries"]
        n = len(entries)
        left, center = index * panel, (index + 0.5) * panel
        numbers = {entry["entry_id"]: i for i, entry in enumerate(entries, 1)}
        elements.append(_text(center, 25, pool["label"] + " 循環積分與場號對照", 18, limit=50))
        for i, entry in enumerate(entries):
            x = left + 15 + (i % 2) * panel / 2
            elements.append(
                _text(x, 52 + (i // 2) * 22, f"{i + 1} {entry['label']}", 14, "start", 30)
            )
        summaries = ["積分"] if classic else ["勝", "敗", "積分", "名次"]
        widths = [40, *([42] * n), *([31] * len(summaries))]
        x0, y0 = center - sum(widths) / 2, 112
        for i in range(n + 1):
            x = x0
            headings = ["選手", *map(str, range(1, n + 1)), *summaries]
            for j, width in enumerate(widths):
                elements.append(
                    f'<rect x="{x:.2f}" y="{y0 + i * 28}" width="{width}" '
                    'height="28" data-text-padding="1.5"/>'
                )
                value = headings[j] if i == 0 else str(i) if j == 0 else "／" if i == j else ""
                if value:
                    elements.append(_text(x + width / 2, y0 + i * 28 + 18, value, 13))
                x += width
        if n == 5 and not classic:
            elements.append(
                _text(center, y0 + (n + 1) * 28 + 22, "勝:2分 敗:1分 棄:0分", 16, limit=50)
            )
        rows = [
            r
            for r in matches
            if r["side_a_ref"].get("entry_id") in numbers
            and r["side_b_ref"].get("entry_id") in numbers
        ]
        for i, row in enumerate(rows):
            if i >= 16:
                raise ValueError("PDF_LAYOUT_OVERFLOW: scoring pair list exceeds its panel")
            value = (
                f"{row['code']}：{numbers[row['side_a_ref']['entry_id']]} 對 "
                f"{numbers[row['side_b_ref']['entry_id']]}"
            )
            if len(_wrap(value, int(panel / 8))) > 1:
                raise ValueError(
                    "PDF_LAYOUT_OVERFLOW: match code is too long for scoring pair list"
                )
            elements.append(
                _text(
                    left + 15 + (i % 2) * panel / 2,
                    330 + (i // 2) * 23,
                    value,
                    14,
                    "start",
                    int(panel / 8),
                )
            )
    return _svg(elements, title)


def _round_robin(
    pools: list[dict],
    matches: list[dict],
    title: str,
    unlabelled_edges: list[dict] | None = None,
    *,
    classic: bool = False,
    scoring_legend: str = "",
) -> str:
    count = len(pools)
    if not 1 <= count <= 3:
        raise ValueError("PDF_LAYOUT_OVERFLOW: use at most three pool panels per page")
    width = 1000 / count
    centers = [(310, 250)] if count == 1 else [((i + 0.5) * width, 170) for i in range(count)]
    elements = []
    occupied: list[tuple[float, float, float, float]] = []

    def bounds(x, y, label, size, anchor="middle", limit=22):
        lines = _wrap(label, limit)
        width = max(sum(2 if ord(c) > 255 else 1 for c in line) for line in lines) * size / 2
        left = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
        return left - 4, y - size - 4, left + width + 4, y + (len(lines) - 1) * (size + 3) + 8

    def overlaps(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    for pool, (cx, cy) in zip(pools, centers, strict=True):
        entries = pool["entries"]
        n = len(entries)
        named_matrix = classic and count == 1 and all(_short_cjk_name(e["label"]) for e in entries)
        if not 2 <= n <= 6:
            raise ValueError("PDF_LAYOUT_OVERFLOW: reference pools require 2–6 entries")
        radius = 200 if count == 1 else 90
        rx = 210 if count == 1 else min(110, width * 0.27)
        vertices = [
            (cx + rx * math.sin(2 * math.pi * i / n), cy - radius * math.cos(2 * math.pi * i / n))
            for i in range(n)
        ]
        by_id = {entry["entry_id"]: point for entry, point in zip(entries, vertices, strict=True)}
        edges = []
        for row in matches:
            a = by_id.get(row["side_a_ref"].get("entry_id"))
            b = by_id.get(row["side_b_ref"].get("entry_id"))
            if a is None or b is None:
                continue
            elements.append(
                _line(*a, *b).replace(
                    "<path ", f'<path data-match-id="{escape(row["match_id"], quote=True)}" ', 1
                )
            )
            edges.append((row, a, b))
        for source_edge in unlabelled_edges or []:
            a = by_id.get(source_edge["side_a_ref"]["entry_id"])
            b = by_id.get(source_edge["side_b_ref"]["entry_id"])
            if a is not None and b is not None:
                elements.append(
                    _line(*a, *b).replace("<path ", '<path data-source-only="true" ', 1)
                )
        for entry_index, (entry, (x, y)) in enumerate(zip(entries, vertices, strict=True), 1):
            vertex_y = y
            horizontal = x - cx
            anchor = "start" if horizontal > 10 else "end" if horizontal < -10 else "middle"
            x += 12 if horizontal > 10 else -12 if horizontal < -10 else 0
            y += -12 if abs(horizontal) < 10 and y < cy else 15
            units = sum(2 if ord(char) > 255 else 1 for char in entry["label"])
            size = 20 if units <= 8 else 16
            label = entry["label"]
            label_limit = 12
            if count == 1:
                size = 20
                label = label if named_matrix else f"{entry_index} {label}"
                # Side labels grow away from the polygon, keeping the region around
                # upper edges and their match numbers clear. Right labels wrap before
                # the scoring table; full names remain together in the SVG text node.
                label_limit = (
                    (10 if units <= 8 else 6) if horizontal > 10 else 9 if horizontal < -10 else 24
                )
                if abs(horizontal) < 10:
                    y = vertex_y - 20 if vertex_y < cy else vertex_y + 36
                else:
                    y = vertex_y + (36 if vertex_y > cy else 6)
            elements.append(_text(x, y, label, size, anchor, label_limit))
            occupied.append(bounds(x, y, label, size, anchor, label_limit))
        # Place the longest internal chords first, then the less constrained outer
        # edges. Labels stay beside their own edge and never overlap another label.
        for row, a, b in sorted(edges, key=lambda edge: -math.dist(edge[1], edge[2])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = max(math.hypot(dx, dy), 1)
            nx, ny = dy / length, -dx / length
            choices = []
            for fraction in (0.5, 1 / 3, 2 / 3, 0.25, 0.75):
                px, py = a[0] + fraction * dx, a[1] + fraction * dy
                sign = 1 if (px - cx) * nx + (py - cy) * ny >= 0 else -1
                for distance in (20, 30, 40):
                    for side in (sign, -sign):
                        x, y = px + nx * distance * side, py + ny * distance * side + 6
                        box = bounds(x, y, row["code"], 18)
                        if (
                            box[0] < 0
                            or box[2] > 1000
                            or box[1] < 45
                            or box[3] > 295 + (100 if count == 1 else 0)
                        ):
                            continue
                        if any(overlaps(box, other) for other in occupied):
                            continue
                        # Prefer the local midpoint; offset/alternate fractions resolve
                        # diameter intersections without changing a match's source edge.
                        penalty = distance + abs(fraction - 0.5) * 60
                        choices.append((penalty, x, y, box))
            if not choices:
                if unlabelled_edges:
                    raise ValueError(
                        "PDF_LAYOUT_OVERFLOW: scoring-only fallback cannot preserve unlabelled source edges"
                    )
                return _round_robin_records(pools, matches, title, classic=classic)
            _, x, y, box = min(choices, key=lambda choice: choice[0])
            occupied.append(box)
            elements.append(
                _text(x, y, row["code"], 18).replace(
                    "<text ",
                    f'<text data-match-id="{escape(row["match_id"], quote=True)}" '
                    'style="paint-order:stroke;stroke:white;stroke-width:5;stroke-linejoin:round" ',
                    1,
                )
            )
        heading = (
            f"{n}取{pool['advancement_count']}進決賽" if pool.get("advancement_count") else "循環賽"
        )
        if pool.get("label"):
            separator = "　" if classic else " · "
            heading = str(pool["label"]) + "池" + separator + heading
        if count > 1:
            elements.append(_text(cx, 30, heading, 21))
        elif pool.get("advancement_count"):
            elements.append(_text(785, 245, heading, 21, limit=32))
        cell, row_height, name_width = 32, 27, 84
        summaries = ["積分"] if classic else ["勝", "敗", "積分", "名次"]
        table_width = name_width + (n + len(summaries)) * cell
        table_center = 785 if count == 1 else cx
        x0, y0 = table_center - table_width / 2, (340 if n <= 5 else 310) if count == 1 else 320
        legend = scoring_legend if classic else "積分紀錄"
        if legend:
            elements.append(_text(table_center, y0 - 13, legend, 16 if classic else 18, limit=36))
        opponents = (
            [e["label"] for e in entries] if named_matrix else [str(i + 1) for i in range(n)]
        )
        headers = ["" if classic else "選手", *opponents, *summaries]
        header_height = 53 if named_matrix else row_height
        for i in range(n + 1):
            label = entries[i - 1]["label"] if i else ""
            label_units = sum(2 if ord(c) > 255 else 1 for c in label)
            row_label = f"{i} {label}" if label_units <= 8 else str(i)
            values = headers if i == 0 else [label if named_matrix else row_label]
            row_y = y0 if i == 0 else y0 + header_height + (i - 1) * row_height
            x = x0
            for j in range(len(headers)):
                column_width = name_width if j == 0 else cell
                elements.append(
                    f'<rect x="{x:.2f}" y="{row_y:.2f}" '
                    f'width="{column_width}" height="{header_height if i == 0 else row_height}" data-text-padding="1.5"/>'
                )
                if j < len(values) and values[j]:
                    elements.append(
                        _text(
                            x + column_width / 2,
                            row_y + (15 if i == 0 and named_matrix else 18),
                            values[j],
                            13,
                            limit=2 if i == 0 and named_matrix else 12,
                        )
                    )
                if i > 0 and j == i:
                    elements.append(
                        f'<line x1="{x:.2f}" y1="{row_y}" '
                        f'x2="{x + column_width:.2f}" y2="{row_y + row_height}" '
                        'stroke="black"/>'
                    )
                x += column_width
        if n == 5 and not classic:
            elements.append(
                _text(
                    table_center,
                    y0 + (n + 1) * row_height + 24,
                    "勝:2分 敗:1分 棄:0分",
                    16,
                    limit=50,
                )
            )
    return _svg(elements, title)


def _knockout(
    rows: list[dict], title: str, *, placement: bool = False, classic: bool = False
) -> str:
    # In a placement stage the match kind describes the whole competition, not
    # a detached bronze playoff. Only typed winner references form tree edges.
    main = {r["match_id"]: r for r in rows if placement or r["kind"] != "季殿／名次賽"}
    placements = [r for r in rows if r["match_id"] not in main]
    if len(placements) > 2:
        raise ValueError("PDF_LAYOUT_OVERFLOW: too many detached placement matches")
    used = {
        side["match_id"]
        for row in main.values()
        for side in (row["side_a_ref"], row["side_b_ref"])
        if side["type"] == "match_winner" and side["match_id"] in main
    }
    roots = sorted((r for mid, r in main.items() if mid not in used), key=lambda r: r["position"])
    leaves: list[tuple[str, str]] = []
    positions: dict[str, tuple[float, int]] = {}
    connections: list[tuple[str, list[tuple[float, int]]]] = []
    visiting: set[str] = set()

    def visit(row: dict) -> tuple[float, int]:
        mid = row["match_id"]
        if mid in visiting or mid in positions:
            raise ValueError("PDF_LAYOUT_OVERFLOW: reference bracket must be a tree")
        visiting.add(mid)
        children = []
        for key in ("side_a", "side_b"):
            ref = row[key + "_ref"]
            if ref["type"] == "match_winner" and ref["match_id"] in main:
                children.append(visit(main[ref["match_id"]]))
            else:
                children.append((float(len(leaves)), 0))
                leaves.append((key, row[key]))
        x, depth = sum(c[0] for c in children) / 2, max(c[1] for c in children) + 1
        positions[mid] = (x, depth)
        connections.append((mid, children))
        visiting.remove(mid)
        return x, depth

    for root in roots:
        visit(root)
    if len(positions) != len(main):
        raise ValueError("PDF_LAYOUT_OVERFLOW: reference bracket contains a cycle")
    if len(leaves) > 16:
        raise ValueError(
            "PDF_LAYOUT_OVERFLOW: reference bracket supports at most 16 entrants per stage"
        )
    depth = max((value[1] for value in positions.values()), default=1)
    vertical_names = (
        classic and len(leaves) > 8 and all(_short_cjk_name(label) for _, label in leaves)
    )
    if len(leaves) > 8 and not vertical_names:
        # Sixteen names cannot share the bottom edge at a readable font size.
        # Turn the tree sideways, preserving the same typed match connections.
        leaf_gap, x0, step = 27, 210, 650 / max(depth, 1)
        elements = []
        for mid, children in connections:
            center, level = positions[mid]
            x = x0 + level * step
            a, b = children
            ay, by = 28 + a[0] * leaf_gap, 28 + b[0] * leaf_gap
            elements.extend(
                [
                    _line(x0 + a[1] * step, ay, x, ay),
                    _line(x, ay, x, by),
                    _line(x, by, x0 + b[1] * step, by),
                    _text(x + 6, 24 + center * leaf_gap, main[mid]["code"], 18, "start"),
                ]
            )
        for i, (_, label) in enumerate(leaves):
            elements.append(_text(195, 33 + i * leaf_gap, label, 20, "end", 20))
        for i, row in enumerate(placements):
            elements.append(
                _text(
                    500,
                    475 + i * 28,
                    f"季殿／名次賽 {row['code']}：{row['side_a']} 對 {row['side_b']}",
                    16,
                    limit=100,
                )
            )
        return _svg(elements, title)
    gap = min(120, 840 / max(len(leaves) - 1, 1))
    left = (1000 - gap * max(len(leaves) - 1, 0)) / 2
    bottom, step = 360, min(100, 265 / max(depth, 1))
    elements = []
    for mid, children in connections:
        x, level = positions[mid]
        y = bottom - level * step
        a, b = children
        ax, bx = left + a[0] * gap, left + b[0] * gap
        elements.extend(
            [
                _line(ax, bottom - a[1] * step, ax, y),
                _line(ax, y, bx, y),
                _line(bx, y, bx, bottom - b[1] * step),
                _text(left + x * gap, y + 22, main[mid]["code"], 18),
            ]
        )
    for i, (_, label) in enumerate(leaves):
        elements.append(
            _text(left + i * gap, bottom + 30, label, 20, limit=2 if vertical_names else 10)
        )
    for i, row in enumerate(placements):
        placement_sources = {row[key + "_ref"].get("match_id") for key in ("side_a", "side_b")}
        root_sources = (
            {roots[0][key + "_ref"].get("match_id") for key in ("side_a", "side_b")}
            if len(roots) == 1
            else set()
        )
        is_bronze = (
            all(row[key + "_ref"]["type"] == "match_loser" for key in ("side_a", "side_b"))
            and len(roots) == 1
            and all(
                roots[0][key + "_ref"]["type"] == "match_winner"
                and roots[0][key + "_ref"].get("match_id") in positions
                for key in ("side_a", "side_b")
            )
            and placement_sources == root_sources
            and None not in root_sources
        )
        if is_bronze:
            root = roots[0]
            a = positions[root["side_a_ref"]["match_id"]]
            b = positions[root["side_b_ref"]["match_id"]]
            y = bottom - positions[root["match_id"]][1] * step + 35
            line = _line(left + a[0] * gap, y, left + b[0] * gap, y)
            elements.append(
                line.replace("<path ", '<path class="bronze-connector" ', 1) if classic else line
            )
            elements.append(
                _text(
                    left + (a[0] + b[0]) / 2 * gap,
                    y + 20,
                    row["code"] if classic else row["code"] + " 季殿",
                    16,
                )
            )
            continue
        elements.append(
            _text(
                500,
                455 + i * 30,
                f"季殿／名次賽　{row['code']}　{row['side_a']}　對　{row['side_b']}",
                16,
                limit=100,
            )
        )
    return _svg(elements, title)


def _validated_unlabelled_edges(model: dict) -> dict[str, list[dict]]:
    """Source-only geometry never implies a match or a completed round robin."""
    stages = {stage["id"]: stage for stage in model.get("stages", [])}
    grouped: dict[str, list[dict]] = {}
    seen: set[tuple[str, frozenset[str]]] = set()
    coded_pairs = {
        (row["stage_id"], frozenset((row["side_a_ref"]["entry_id"], row["side_b_ref"]["entry_id"])))
        for row in model["matches"]
        if all(row[key + "_ref"].get("type") == "entry" for key in ("side_a", "side_b"))
    }
    for edge in model.get("unlabelled_edges", []):
        stage_id = edge.get("stage_id")
        stage = stages.get(stage_id)
        if stage is None or stage["type"] != "round_robin":
            raise ValueError("SOURCE_EDGE_INVALID: unknown or non-round-robin stage")
        rows = [row for row in model["matches"] if row["stage_id"] == stage_id]
        if not rows:
            raise ValueError("SOURCE_EDGE_INVALID: stage has no coded matches to render")
        refs = [edge.get(key + "_ref", {}) for key in ("side_a", "side_b")]
        if any(ref.get("type") != "entry" or not ref.get("entry_id") for ref in refs):
            raise ValueError("SOURCE_EDGE_INVALID: endpoints must be typed entry references")
        first, second = (ref["entry_id"] for ref in refs)
        if first == second:
            raise ValueError("SOURCE_EDGE_INVALID: source edge is a self-pair")
        pools = [pool for pool in model.get("pools", []) if pool["stage_id"] == stage_id]
        if not pools:
            # Match the renderer's existing single-pool inference, using identities.
            pools = [
                {
                    "entries": [
                        ref
                        for row in rows
                        for ref in (row["side_a_ref"], row["side_b_ref"])
                        if ref.get("type") == "entry"
                    ]
                }
            ]
        memberships = [
            {
                index
                for index, pool in enumerate(pools)
                if any(entry["entry_id"] == identifier for entry in pool["entries"])
            }
            for identifier in (first, second)
        ]
        if len(memberships[0]) != 1 or memberships[0] != memberships[1]:
            raise ValueError("SOURCE_EDGE_INVALID: endpoints must exist in the same unique pool")
        pair = (stage_id, frozenset((first, second)))
        if pair in coded_pairs or pair in seen:
            raise ValueError("SOURCE_EDGE_INVALID: duplicate coded or unlabelled pair")
        if not edge.get("source_ref"):
            raise ValueError("SOURCE_EDGE_INVALID: unlabelled edge requires source provenance")
        seen.add(pair)
        grouped.setdefault(stage_id, []).append(edge)
    return grouped


def graph_pages(model: dict, directory: Path, *, classic: bool = False) -> list[dict]:
    pages: list[dict[str, Any]] = []
    source_edges = _validated_unlabelled_edges(model)
    stages = model.get("stages", [])
    pool_labels = {pool["id"]: pool["label"] for pool in model.get("pools", []) if "id" in pool}
    division_ids = [d["id"] for d in model.get("divisions", [])]
    division_ids.extend(s["division_id"] for s in stages if s["division_id"] not in division_ids)
    order = {division_id: i for i, division_id in enumerate(dict.fromkeys(division_ids))}
    for stage in sorted(
        stages,
        key=lambda item: (
            order[item["division_id"]],
            item["order"],
            item.get("label") or "",
            item["id"],
        ),
    ):
        rows = [r for r in model["matches"] if r["stage_id"] == stage["id"]]
        if not rows:
            continue
        stage_page_start = len(pages)
        division = rows[0]["division"]
        name = stage.get("label") or stage.get("display_name") or rows[0]["stage"]
        if name == "原稿未另標階段":
            name = "循環賽" if stage["type"] == "round_robin" else "對戰圖"
        if name == "淘汰賽":
            name = "決賽"
            if classic and any(row["kind"] == "季殿／名次賽" for row in rows):
                has_pools = any(
                    s["division_id"] == stage["division_id"] and s["type"] == "round_robin"
                    for s in stages
                )
                name = "決賽（一至四名）" if has_pools else "一至四名"
        elif name == "循環賽" and any(
            s["division_id"] == stage["division_id"] and s["order"] > stage["order"] for s in stages
        ):
            name = "預賽"
        title = division if classic and name == "循環賽" else division + "　" + name
        metadata = {
            "division_id": stage["division_id"],
            "stage_id": stage["id"],
            "source_page": stage.get("source_page"),
            "stage_type": stage["type"],
        }
        references: list[tuple[str, str]] = []

        def short(label: str, limit: int = 8, refs=references) -> str:
            if sum(2 if ord(c) > 255 else 1 for c in label) <= limit:
                return label
            for marker, full in refs:
                if full == label:
                    return marker
            marker = f"參照{len(refs) + 1}"
            refs.append((marker, label))
            return marker

        notes = []
        draw_ids = {
            r[key + "_ref"].get("draw_run_id") for r in rows for key in ("side_a", "side_b")
        }
        for draw in model.get("deferrals", []):
            if draw.get("id") not in draw_ids:
                continue
            procedure = draw.get("procedure", {})
            notes.append("後續抽籤程序：" + procedure.get("description", "程序尚未設定"))
            if procedure.get("execution_trigger") == "after_sources_confirmed":
                notes.append(f"待來源資格確認後執行；預留 {procedure['duration_minutes']} 分鐘。")
            if procedure.get("earliest_at"):
                notes.append("最早執行時間：" + str(procedure["earliest_at"]))
            notes.append("抽籤紀錄狀態：" + str(draw.get("status", "待確認")))
        if stage["type"] == "round_robin":
            scope = f"{division} · {rows[0]['stage']}"
            scoring_lines = {
                line.removeprefix("積分：")
                for rule in model.get("rule_summaries", [])
                if scope in rule.get("scopes", [rule["title"]])
                for line in rule["lines"]
                if line.startswith("積分：")
            }
            scoring_legend = next(iter(scoring_lines)) if len(scoring_lines) == 1 else ""
            points = re.fullmatch(r"勝 (-?\d+)、敗 (-?\d+)、棄 (-?\d+)", scoring_legend)
            if points:
                scoring_legend = "勝:{}分　敗:{}分　棄:{}分".format(*points.groups())
            pools = [p for p in model["pools"] if p["stage_id"] == stage["id"]]
            if not pools:
                entries = {}
                for row in rows:
                    for side in ("side_a", "side_b"):
                        ref = row[side + "_ref"]
                        if ref["type"] == "entry":
                            entries[ref["entry_id"]] = {
                                "entry_id": ref["entry_id"],
                                "label": row[side],
                            }
                pools = [
                    {"entries": list(entries.values()), "label": "", "advancement_count": None}
                ]
            if not pools or any(not 2 <= len(p["entries"]) <= 6 for p in pools):
                raise ValueError(
                    "PDF_LAYOUT_OVERFLOW: reference pool graphs require pools of 2–6 entries"
                )
            capacity = 2 if any(len(p["entries"]) > 3 for p in pools) else 3
            for offset in range(0, len(pools), capacity):
                part_count = (len(pools) + capacity - 1) // capacity
                panel_title = title + (
                    f"（{offset // capacity + 1}/{part_count}）" if part_count > 1 else ""
                )
                batch = pools[offset : offset + capacity]
                group = [
                    {
                        **p,
                        "entries": [
                            {**e, "label": short(e["label"], 20 if len(batch) == 1 else 8)}
                            for e in p["entries"]
                        ],
                    }
                    for p in batch
                ]
                entry_ids = {e["entry_id"] for p in group for e in p["entries"]}
                match_ids = [
                    r["match_id"]
                    for r in rows
                    if all(
                        r[key + "_ref"].get("entry_id") in entry_ids for key in ("side_a", "side_b")
                    )
                ]
                unlabelled = [
                    edge
                    for edge in source_edges.get(stage["id"], [])
                    if all(
                        edge[key + "_ref"]["entry_id"] in entry_ids for key in ("side_a", "side_b")
                    )
                ]
                pages.append(
                    {
                        **metadata,
                        "title": panel_title,
                        "svg": _round_robin(
                            group,
                            rows,
                            panel_title,
                            unlabelled,
                            classic=classic,
                            scoring_legend=scoring_legend,
                        ),
                        "notes": list(notes)
                        + (
                            ["姓名前數字僅供積分表對照，不代表種子、抽籤位置或名次。"]
                            if len(group) == 1 and not classic
                            else []
                        )
                        + (["原稿有未編號連線，未建立正式場次"] if unlabelled else []),
                        "match_ids": match_ids,
                        "unlabelled_edge_count": len(unlabelled),
                    }
                )
        else:

            def leaf_label(row, side):
                ref = row[side + "_ref"]
                pool = pool_labels.get(ref.get("pool_id"), "")
                if classic and ref["type"] == "pool_rank" and re.fullmatch(r"[A-Z]", pool):
                    return f"{pool}{ref['rank']}"
                return short(row[side], 12)

            printed = [
                {**r, "side_a": leaf_label(r, "side_a"), "side_b": leaf_label(r, "side_b")}
                for r in rows
            ]
            pages.append(
                {
                    **metadata,
                    "title": title,
                    "svg": _knockout(
                        printed, title, placement=stage["type"] == "placement", classic=classic
                    ),
                    "notes": notes,
                    "match_ids": [r["match_id"] for r in rows],
                }
            )
        # A short key belongs next to its graph; reserve separate sheets for long labels.
        if len(references) <= 4 and sum(len(full) for _, full in references) <= 180:
            for page in pages[stage_page_start:]:
                page["notes"].extend(
                    f"{marker}：{full}" for marker, full in references if marker in page["svg"]
                )
            references = []
        # Full labels stay searchable and editable; do not squeeze them into a bracket gap.
        chunks: list[list[str]] = [[]]
        units = 0
        for marker, label in references:
            lines = _wrap(f"{marker}：{label}", 100)
            for line in lines:
                if units == 14:
                    chunks.append([])
                    units = 0
                chunks[-1].append(line)
                units += 1
        for chunk in chunks:
            if chunk:
                pages.append(
                    {
                        **metadata,
                        "title": title + "　姓名／來源參照",
                        "svg": _svg(
                            [
                                _text(30, 35 + i * 34, line, 18, "start", 100)
                                for i, line in enumerate(chunk)
                            ],
                            title,
                        ),
                        "notes": [],
                        "match_ids": [],
                    }
                )
    if sum(page.get("unlabelled_edge_count", 0) for page in pages) != sum(
        len(edges) for edges in source_edges.values()
    ):
        raise ValueError("SOURCE_EDGE_INVALID: source edge coverage omits or repeats a line")
    for index, page in enumerate(pages, 1):
        filename = f"bracket-reference-{index:03}.svg"
        (directory / filename).write_text(page["svg"], "utf-8")
        page["file"] = filename
    return pages


def schedule_pages(model: dict, *, classic: bool = False) -> list[dict]:
    pages = []
    dates = list(dict.fromkeys(row["date"] for row in model["matches"]))
    for day in dates:
        rows = [row for row in model["matches"] if row["date"] == day]
        max_rows = math.ceil(len(rows) / math.ceil(len(rows) / 34)) if classic else 26
        chunk: list[dict] = []
        units = 0
        for row in rows:
            # Reserve height for every wrapped line; never shrink fonts to fit.
            widths = (9, 16, 14, 24, 24) if classic else (12, 10, 10, 16, 23, 23)
            cells = schedule_cells(row, classic=classic)
            height = max(
                len(_wrap(value, width)) for value, width in zip(cells, widths, strict=True)
            )
            if height > 12:
                raise ValueError("PDF_LAYOUT_OVERFLOW: reference schedule cell is too tall")
            if chunk and (units + height > 38 or len(chunk) >= max_rows):
                pages.append({"date": day, "title": date_label(day), "rows": chunk})
                chunk, units = [], 0
            chunk.append({"cells": cells, "match_id": row["match_id"]})
            units += height
        if chunk:
            pages.append({"date": day, "title": date_label(day), "rows": chunk})
    return pages


TEMPLATE = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>{{ name }} · {{ 'DRAFT' if draft else '核准版本' }}</title><style>
@page {size:A4 landscape;margin:12mm}
@page graph {size:A4 landscape;margin:12mm}
@page schedule {size:A4 portrait;margin:12mm}
*{box-sizing:border-box}body{margin:0;color:#000;font:12pt 'DFKai-SB','BiauKai',serif}
.sheet{break-before:page}.sheet:first-child{break-before:auto}.graph-sheet{page:graph}
.schedule-sheet,.notice-sheet,.rules-sheet,.front-sheet{page:schedule}
h1{font-size:24pt;text-align:center;margin:2mm 0 5mm;font-weight:bold}
.graph-sheet h1{font-size:30pt;line-height:1.2;margin:0 0 4mm}
.graph svg{display:block;width:100%;height:auto;max-height:145mm}
.graph-notes{font-size:10pt;line-height:1.35;margin:1mm 0}
.stamp{text-align:center;font-size:9pt;margin:2mm 0;color:#555}
.date{font-size:21pt;font-weight:normal;letter-spacing:1px;margin-bottom:1mm}
.schedule-date{text-align:center;font-size:9pt;margin:0 0 2mm}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:11pt}
th,td{border:0.6pt solid #000;text-align:center;vertical-align:top;overflow-wrap:anywhere;padding:1.1mm 0.8mm;line-height:1.2}
td:first-child{white-space:nowrap}
th{background:#d9d9d9;font-weight:normal}td{min-height:6mm}thead{display:table-header-group}tr{break-inside:avoid}
.identity{font-size:9pt;overflow-wrap:anywhere;line-height:1.5}
.cover{padding-top:32mm;text-align:center}.cover h1{font-size:32pt;line-height:1.55;border-top:2pt solid #000;border-bottom:1pt solid #000;padding:12mm 0;margin-bottom:18mm}
.cover .subtitle{font-size:21pt;letter-spacing:3px;margin-bottom:20mm}
.cover p{font-size:14pt;line-height:1.65}.cover .stamp{font-size:11pt}
.front-sheet h2{font-size:16pt;margin:5mm 0 2mm;break-after:avoid}
.front-sheet p{font-size:11pt;line-height:1.5;margin:1.5mm 0}
.front-sheet .coverage{border-left:2pt solid #000;padding-left:4mm;margin:8mm 0;font-size:10pt}
.contents{font-size:12pt;line-height:1.5}
.contents a{display:block;position:relative;color:#000;text-decoration:none;padding:1mm 15mm 1mm 0;break-inside:avoid;page-break-inside:avoid;overflow-wrap:anywhere;border-bottom:0.4pt dotted #777}
.contents [data-toc-page]{position:absolute;right:0;top:1mm;width:12mm;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.contents-heading{font-size:10pt!important;text-align:right;margin:0!important}
.prospectus-item{break-inside:avoid}
.rule-scope{font-size:10pt!important;line-height:1.45!important;margin-bottom:4mm!important}
.rule-group{break-inside:avoid;margin:5mm 0 8mm}
.rule-lines p{font-size:11pt;line-height:1.45;margin:1.5mm 0}
.rules-sheet h2{font-size:15pt;margin:6mm 0 2mm}.notice-sheet{text-align:center}
.qr{width:115mm;height:115mm;margin-top:10mm}.sponsor-asset{max-width:150mm;max-height:50mm}
.notice-sheet p{font-size:14pt;overflow-wrap:anywhere}
{{ font_css | safe }}
.classic .graph-sheet h1,.classic .graph svg text,.classic .schedule-sheet{font-family:{{ kai_fonts | safe }} !important}
.classic .graph-sheet h1{font-weight:700}
.classic .graph-elimination svg path,.classic .graph-placement svg path{stroke-width:4}
.classic .graph svg path.bronze-connector{stroke-width:1;stroke-dasharray:2 2}
.classic .schedule{width:calc(100% - 1mm);border-collapse:separate;border-spacing:0;border:0.6pt solid #000}
.classic .schedule td,.classic .schedule th{border:0;border-right:0.6pt solid #000;border-bottom:0.6pt solid #000;padding:0.9mm 0.8mm;font-size:11.5pt}
.classic .schedule tr>:last-child{border-right:0}.classic .schedule tbody tr:last-child td{border-bottom:0}
.classic .schedule-date{margin-bottom:2.5mm}
</style>
<body{% if classic %} class="classic"{% endif %}>
{% if front_matter %}
<section class="sheet front-sheet cover" id="cover"><h1>{{ name }}</h1><div class="subtitle">{{ '競賽簡章暨秩序冊' if publication.document_profile == 'prospectus' else '比賽秩序冊' }}</div>
<p>{% for day in event_dates %}{{ day }}{% if not loop.last %} ～ {% endif %}{% endfor %}</p><p>{{ venue }}</p>
{% if draft %}<p class="stamp">審閱草案 · 尚未核准，不可作正式比賽公告</p>{% if publication.draft_note %}<p class="stamp">{{ publication.draft_note }}</p>{% endif %}{% endif %}</section>
<section class="sheet front-sheet" id="contents"><h1>目錄與賽事概要</h1>
<p class="contents-heading">頁碼</p>
<div class="contents">
{% if unplayed_divisions %}<a href="#unplayed-divisions" data-toc-target="unplayed-divisions"><span data-toc-title>未編對戰組別</span><span data-toc-page>00</span></a>{% endif %}
{% if publication.document_profile == 'prospectus' or prospectus.configured %}<a href="#prospectus" data-toc-target="prospectus"><span data-toc-title>競賽辦法</span><span data-toc-page>00</span></a>{% endif %}
<a href="#rules" data-toc-target="rules"><span data-toc-title>比賽與排名規則</span><span data-toc-page>00</span></a>
{% for graph in graphs %}<a href="#graph-{{ loop.index }}" data-toc-target="graph-{{ loop.index }}"><span data-toc-title>{{ graph.title }}</span><span data-toc-page>00</span></a>{% endfor %}
{% for sheet in schedule_pages %}{% if loop.first or sheet.date != schedule_pages[loop.index0 - 1].date %}<a href="#schedule-{{ sheet.date }}-{{ loop.index }}" data-toc-target="schedule-{{ sheet.date }}-{{ loop.index }}"><span data-toc-title>每日比賽時間表 · {{ sheet.title }}</span><span data-toc-page>00</span></a>{% endif %}{% endfor %}
{% if qr_data or sponsors %}<a href="#notices" data-toc-target="notices"><span data-toc-title>{{ '場次通知 QR Code 與相關資訊' if qr_data else '相關資訊' }}</span><span data-toc-page>00</span></a>{% endif %}
</div>
<h2>比賽場地</h2><p>{% for court in courts %}{{ court.label }}{% if not loop.last %}　／　{% endif %}{% endfor %}</p>
{% if coverage_note %}<p class="coverage">{{ coverage_note }}</p>{% endif %}
{% if draft %}<p class="stamp">抽籤、場次與比賽辦法以主辦單位核准發布版本為準。</p>{% endif %}</section>
{% if unplayed_divisions %}<section class="sheet front-sheet" id="unplayed-divisions"><h1>未編對戰組別</h1><p>以下保留報名名單；本草稿未編對戰，組別處置以主辦單位核定為準。</p>
{% for division in unplayed_divisions %}<div class="prospectus-item"><h2>{{ division.name }}（{{ division.names | length }} 筆報名）</h2><p>{{ division.names | join('、') or '無報名名單' }}</p></div>{% endfor %}</section>{% endif %}
{% if publication.document_profile == 'prospectus' or prospectus.configured %}
<section class="sheet front-sheet" id="prospectus"><h1>競賽辦法</h1>
{% if not prospectus.approved %}<p class="stamp">主辦條款尚未核准；下列待補內容不代表正式競賽辦法。</p>{% endif %}
{% for item in prospectus.sections %}<div class="prospectus-item"><h2>{{ loop.index }}、{{ item.title }}</h2>{% for line in item.lines %}<p>{{ line }}</p>{% endfor %}</div>{% endfor %}</section>
{% endif %}
<section class="sheet rules-sheet" id="rules"><h1>比賽與排名規則</h1>
{% for rule in rule_summaries %}<div class="rule-group"><h2>{{ '各組共通規則' if rule.scopes|default([])|length > 1 else rule.title }}</h2>
{% if rule.scopes|default([])|length > 1 %}<p class="rule-scope">適用：{{ rule.scopes | join('、') }}</p>{% endif %}
<div class="rule-lines">{% for line in rule.lines %}<p>{{ line }}</p>{% endfor %}</div></div>{% endfor %}</section>
{% endif %}
{% for graph in graphs %}<section class="sheet graph-sheet" id="graph-{{ loop.index }}"><h1>{{ graph.title }}</h1>
<div class="graph graph-{{ graph.stage_type }}">{{ graph.svg | safe }}</div>
{% for note in graph.notes | default([]) %}<p class="graph-notes">{{ note }}</p>{% endfor %}
{% if draft %}<p class="stamp">{% if classic %}審閱草案 · 尚未核准{% else %}DRAFT 草案 · {{ publication.draft_note or name }} · 尚未核准{% endif %}</p>{% endif %}</section>{% endfor %}
{% for sheet in schedule_pages %}<section class="sheet schedule-sheet" id="schedule-{{ sheet.date }}-{{ loop.index }}"><h1 class="date">{{ sheet.title }}</h1>
<p class="schedule-date">{% if classic %}預計時間僅供參考，請以場次為主。比賽開始後以參賽者網頁為準。{% if draft %}（審閱草案）{% endif %}{% else %}{{ name }} · {{ sheet.date }}{% if draft %} · DRAFT 草案{% endif %}{% endif %}</p>
<table class="schedule"><colgroup>{% for width in schedule_widths %}<col style="width:{{ width }}%">{% endfor %}</colgroup>
<thead><tr><th>場次</th><th>預計時間</th>{% if not classic %}<th>球場</th>{% endif %}<th>組別</th><th colspan="2">選手</th></tr></thead>
<tbody>{% for row in sheet.rows %}<tr data-match-id="{{ row.match_id }}">{% for cell in row.cells %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table></section>{% endfor %}
{% if qr_data or sponsors %}<section class="sheet notice-sheet" id="notices"><h1>{{ name }}場次通知</h1>
{% if qr_data %}<p>{{ publication.qr_label }}</p><img class="qr" src="{{ qr_data }}" alt="通知網址 QR Code"><p>{{ publication.qr_url }}</p>{% endif %}
{% for sponsor in sponsors %}<p>{{ sponsor.display_name }}　{{ sponsor.text }}</p>{% if sponsor.image_data %}<img class="sponsor-asset" src="{{ sponsor.image_data }}" alt="{{ sponsor.display_name }}">{% endif %}{% endfor %}</section>{% endif %}
{% if not front_matter %}<section class="sheet rules-sheet"><h1>比賽規則與版本紀錄</h1>
<p class="identity">{{ name }} · {{ venue }} · {{ timezone }}<br>Revision {{ revision_id }}<br>Content {{ content_hash }}</p>
{% if draft %}<p class="stamp">DRAFT 草案 · 合成原型或尚未核准 · 不可作正式公告</p>{% endif %}
{% for rule in rule_summaries %}<h2>{{ rule.title }}</h2><div class="rule-lines">{% for line in rule.lines %}<p>{{ line }}</p>{% endfor %}</div>{% endfor %}
{% if issues %}<h2>待處理事項</h2>{% for issue in issues %}<p>{{ issue_label(issue.code) }}</p>{% endfor %}{% endif %}
{% if roster_candidates %}<h2>待確認名單</h2>{% for c in roster_candidates %}<p>{{ c.division }} · {{ c.official_name }} · {{ c.source_name }} · {{ c.position }} · {{ c.status }}</p>{% endfor %}{% endif %}
</section>{% endif %}</html>"""


def render(context: dict, *, font_css: str = "") -> str:
    widths = CLASSIC_SCHEDULE_WIDTHS if context.get("classic") else SCHEDULE_WIDTHS
    return (
        Environment(autoescape=True)
        .from_string(TEMPLATE)
        .render(
            **context,
            font_css=font_css,
            kai_fonts=KAI_FONTS,
            schedule_widths=[w * 100 for w in widths],
        )
    )
