"""Explicit, division-scoped seed configuration before competition starts."""

from .config import DrawConfig
from .models import Snapshot


def configure_seeds(snapshot: Snapshot, division_id: str, mode: str, seeds: list[dict]) -> dict:
    division = next(d for d in snapshot.divisions if d.id == division_id)
    matches = {m.id for m in snapshot.matches if m.division_id == division_id}
    if (
        any(m.id in matches and m.status in {"in_progress", "completed"} for m in snapshot.matches)
        or any(r.get("match_id") in matches for r in snapshot.results)
        or any(
            a.match_id in matches and (a.locks.get("draw") or a.locks.get("sides"))
            for a in snapshot.assignments
        )
    ):
        raise ValueError("此組已有開打、成績或籤位鎖定，不能直接修改種子；須由賽務處理。")
    if mode not in {"none", "provided"} or not isinstance(seeds, list):
        raise ValueError("請明確選擇無種子或提供種子名單。")
    rules = DrawConfig.model_validate(
        {
            **snapshot.config.get("draw_defaults", {}),
            **division.draw_overrides,
            "seeded_entries_mode": mode,
            "seeded_entries": seeds,
        }
    )
    capacity = (division.format_plan or {}).get("bracket_capacity")
    entries = {
        e.id
        for e in snapshot.entries
        if e.effective_division_id == division_id and e.status == "active"
    }
    template = (division.format_plan or {}).get("template_id", "")
    if template == "round_robin" or template.startswith("pools_"):
        capacity = len(entries)
    identifiers, numbers = set(), set()
    if (mode == "none" and seeds) or (mode == "provided" and not seeds):
        raise ValueError("種子模式與名單不一致。")
    for seed in seeds:
        identifier, number = seed.get("entry_id"), seed.get("seed_number")
        positions = seed.get("allowed_positions")
        if (
            not isinstance(identifier, str)
            or identifier not in entries
            or identifier in identifiers
            or type(number) is not int
            or number < 1
            or number in numbers
            or not isinstance(positions, list)
            or not positions
            or len({str(p) for p in positions}) != len(positions)
            or type(capacity) is not int
            or any(not str(p).isdigit() or not 1 <= int(p) <= capacity for p in positions)
        ):
            raise ValueError(
                "種子須為本組有效選手，序號不可重複，允許籤位須在籤表範圍內且不可重複。"
            )
        identifiers.add(identifier)
        numbers.add(number)
    before = {
        key: division.draw_overrides.get(key, snapshot.config.get("draw_defaults", {}).get(key))
        for key in ("seeded_entries_mode", "seeded_entries")
    }
    before["seeded_entries"] = before["seeded_entries"] or []
    if before == {"seeded_entries_mode": mode, "seeded_entries": seeds}:
        return {
            "before": before,
            "after": before,
            "invalidated_draw_ids": [],
            "requires_redraw": False,
        }
    division.draw_overrides.update(
        seeded_entries_mode=rules.seeded_entries_mode, seeded_entries=rules.seeded_entries
    )
    invalidated = []
    for draw in snapshot.draws:
        if draw.get("division_id") == division_id and draw.get("kind") not in {
            "encouragement",
            "deferred_slots",
        }:
            if draw.get("status") != "superseded":
                invalidated.append(draw["id"])
            draw["status"] = "superseded"
            for action in snapshot.approvals:
                if action.get("target_id") == draw["id"]:
                    action["status"] = "stale"
    return {
        "before": before,
        "after": {"seeded_entries_mode": mode, "seeded_entries": seeds},
        "invalidated_draw_ids": invalidated,
        "requires_redraw": bool(matches or invalidated),
    }
