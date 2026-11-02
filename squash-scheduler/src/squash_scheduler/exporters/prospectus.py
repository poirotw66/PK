"""Public prospectus content with explicit unknowns and scoped, readable match rules."""

from __future__ import annotations

import re
from typing import Any

from ..domain.models import Snapshot

PENDING = "待補（須由主辦單位確認）"
DEMO_PENDING = "示範條款，待主辦單位確認；不作為正式競賽規則"
SECTION_FIELDS = (
    ("organizers", "主辦單位"),
    ("co_organizers", "協辦單位"),
    ("eligibility", "參賽資格"),
    ("registration", "報名辦法"),
    ("check_in", "報到辦法"),
    ("competition", "競賽辦法"),
    ("awards", "獎勵辦法"),
    ("appeals", "申訴辦法"),
    ("contact", "聯絡資訊"),
)
_INTERNAL_WORDS = re.compile(
    r"\b(?:demo_only|synthetic_only|grace_minutes|automatic)\b", re.IGNORECASE
)
_METADATA_FLAGS = frozenset({"demo_only", "synthetic_only"})
_PLACEHOLDER = re.compile(
    r"待補|待確認|尚未設定|尚未確認|未核定|待核定|\b(?:TODO|TBD)\b", re.IGNORECASE
)
_LABELS = {
    "include_in_rankings": "計入排名",
    "counts_as_played": "計入實際出賽",
    "winner_points": "勝方積分",
    "loser_points": "敗方積分",
    "side_a_points": "甲方積分",
    "side_b_points": "乙方積分",
    "walkover": "棄權",
    "retired": "受傷退賽",
    "double_forfeit": "雙方棄權",
    "manual_review": "由賽務人員處理",
    "minutes": "分鐘",
    "grace_minutes": "遲到寬限分鐘",
    "automatic": "自動處理",
    "action": "處置",
    "mode": "方式",
    "description": "說明",
    "note": "說明",
    "order": "比較順序",
    "head_to_head": "相互對戰",
    "game_difference": "淨局數",
    "point_difference": "淨分數",
}


def contains_demo_rule(value: Any) -> bool:
    """Keep the demonstration evidence separate from printable text."""
    if isinstance(value, dict):
        return bool(value.get("demo_only")) or any(contains_demo_rule(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_demo_rule(v) for v in value)
    return False


def public_rule_text(value: Any) -> str:
    """Render an allowlist of public fields, never arbitrary engineering metadata."""
    if value is None:
        return PENDING
    if isinstance(value, dict):
        if value.get("demo_only"):
            return DEMO_PENDING
        parts = [
            f"{_LABELS[key]}：{public_rule_text(item)}"
            for key, item in value.items()
            if key in _LABELS
        ]
        if any(key not in _LABELS and key not in _METADATA_FLAGS for key in value):
            # Unknown fields may encode material terms. A partial translation
            # must not look complete enough to publish as an approved rule.
            parts.append(PENDING)
        return "；".join(parts) or PENDING
    if isinstance(value, list):
        return "、".join(public_rule_text(item) for item in value) or PENDING
    if isinstance(value, bool):
        return "是" if value else "否"
    text = _LABELS.get(str(value).strip(), str(value).strip())
    if _INTERNAL_WORDS.search(text):
        return PENDING
    return text or PENDING


def _paragraphs(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [text for item in values if item is not None and (text := str(item).strip())]


def prospectus_view(config: dict[str, Any]) -> dict[str, Any]:
    """Accept the event configuration and return template-ready prospectus sections."""
    publication = config.get("publication", {})
    content = publication.get("prospectus") or {}
    sections, missing = [], []
    for field, title in SECTION_FIELDS:
        paragraphs = _paragraphs(content.get(field))
        if not paragraphs or any(
            _INTERNAL_WORDS.search(p) or _PLACEHOLDER.search(p) for p in paragraphs
        ):
            paragraphs = [PENDING]
            missing.append(field)
        sections.append({"field": field, "title": title, "lines": paragraphs})
    return {
        "sections": sections,
        "missing_fields": missing,
        "approved": content.get("approval_status") == "approved",
        "complete": not missing,
        "configured": publication.get("prospectus") is not None,
    }


def publication_content_issues(config: dict[str, Any]) -> list[str]:
    """Formal publication requirements; legacy order books remain compatible."""
    publication = config.get("publication", {})
    view = prospectus_view(config)
    if publication.get("document_profile", "order_book") != "prospectus" and not view["configured"]:
        return []
    issues = ["publication.prospectus." + field for field in view["missing_fields"]]
    if not view["approved"]:
        issues.append("publication.prospectus.approval_status")
    return issues


def merge_rule_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge identical text and approval state while retaining every division/stage scope."""
    merged: dict[tuple, dict[str, Any]] = {}
    for summary in summaries:
        lines = list(dict.fromkeys(summary["lines"]))
        key = (tuple(lines), bool(summary.get("is_demo", False)))
        scopes = summary.get("scopes") or [summary["title"]]
        if key not in merged:
            merged[key] = {"title": "", "lines": lines, "scopes": [], "is_demo": key[1]}
        for scope in scopes:
            if scope not in merged[key]["scopes"]:
                merged[key]["scopes"].append(scope)
        merged[key]["title"] = "、".join(merged[key]["scopes"])
    return list(merged.values())


def rule_summary(snapshot: Snapshot) -> list[dict[str, Any]]:
    """Use frozen effective match rules, never invent missing tie or forfeiture terms."""
    divisions = {division.id: division.name for division in snapshot.divisions}
    stages = {stage.id: stage for stage in snapshot.stages}
    stage_labels = {"round_robin": "循環賽", "elimination": "淘汰賽", "placement": "名次賽"}
    summaries = []
    for match in snapshot.matches:
        if match.status in {"cancelled", "excluded"} or not match.rule_snapshot:
            continue
        rules = match.rule_snapshot.get("match_rules", {})
        best = rules.get("best_of_games")
        target, margin = rules.get("points_to_win_game"), rules.get("win_by_points")
        scoring = rules.get("scoring", {})
        lines = [
            f"{best} 局 {best // 2 + 1} 勝" if best else "局數：" + PENDING,
            f"每局 {target} 分、領先 {margin} 分" if target and margin else "局分：" + PENDING,
            f"封頂 {rules['point_cap']} 分"
            if rules.get("point_cap_mode") == "enabled" and rules.get("point_cap")
            else "不設封頂"
            if rules.get("point_cap_mode") == "none"
            else "封頂：" + PENDING,
            "積分：勝 {}、敗 {}、棄 {}".format(
                *(
                    scoring.get(field, PENDING)
                    for field in ("win_points", "loss_points", "forfeit_points")
                )
            ),
        ]
        for label, field in (
            ("遲到", "late_arrival_rule"),
            ("棄權", "walkover_rule"),
            ("受傷退賽", "retirement_rule"),
            ("雙方棄權", "double_forfeit_rule"),
            ("補位", "replacement_rule"),
            ("排名分母", "ranking_denominator_rule"),
            ("同分比較", "multiway_tie_rule"),
        ):
            lines.append(label + "：" + public_rule_text(rules.get(field)))
        is_demo = contains_demo_rule(rules)
        if is_demo:
            lines.insert(0, DEMO_PENDING)
        summaries.append(
            {
                "title": divisions[match.division_id]
                + " · "
                + (
                    getattr(stages[match.stage_id], "label", None)
                    or stage_labels[stages[match.stage_id].type]
                ),
                "lines": lines,
                "is_demo": is_demo,
            }
        )
    return merge_rule_summaries(summaries)
