"""Read-only per-person workload audit, with resolved and conservative counts separate."""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise
from typing import Any

from squash_scheduler.domain.models import Snapshot
from squash_scheduler.domain.rankings import resolve_side

from .constraints import build_context, rest_minutes, transfer_minutes, validate_schedule


def audit_workload(snapshot: Snapshot, *, policy: dict | None = None) -> dict[str, Any]:
    working = snapshot.model_copy(deep=True)
    if policy is not None:
        working.config["player_workload"] = policy
    context, issues = build_context(working)
    if context is None:
        return {"status": "invalid", "issues": [i.model_dump(mode="json") for i in issues]}
    people = {p.id: p.display_name for p in working.participants}
    registrations = {r.id: r.participant_id for r in working.registrations}
    entries = {e.id: registrations[e.registration_id] for e in working.entries}
    divisions = {d.id: d.name for d in working.divisions}
    fixed: dict[tuple[str, str], list] = defaultdict(list)
    possible: dict[tuple[str, str], list] = defaultdict(list)
    assigned = {a.match_id: a for a in working.assignments if a.match_id in context.matches}
    for mid, assignment in assigned.items():
        day = assignment.start_at.astimezone(context.zone).date().isoformat()
        match = context.matches[mid]
        resolved_people = {
            entries[e]
            for side in (match.side_a, match.side_b)
            if (e := resolve_side(working, side)) is not None and e in entries
        }
        for person in resolved_people:
            fixed[(person, day)].append(assignment)
        for person in context.candidates.get(mid, set()):
            possible[(person, day)].append(assignment)

    def describe(a):
        match = context.matches[a.match_id]
        return {
            "match_id": match.id,
            "code": match.display_code,
            "division": divisions[match.division_id],
            "start": a.start_at.astimezone(context.zone).isoformat(),
            "play_end": a.play_end_at.astimezone(context.zone).isoformat(),
            "court_id": a.court_id,
        }

    daily, gaps = [], []
    for person, day in sorted(possible):
        known = sorted(fixed[(person, day)], key=lambda a: a.start_at)
        potential = sorted(possible[(person, day)], key=lambda a: a.start_at)
        cap = context.max_matches_per_day
        day_ids = {a.match_id for a in potential}
        compatible_paths = context.player_paths().get(person, [])
        day_path = max((path & day_ids for path in compatible_paths), key=len, default=frozenset())
        daily.append(
            {
                "participant_id": person,
                "name": people[person],
                "date": day,
                "known_count": len(known),
                "candidate_upper_bound": len(potential),
                "known_exceeds_cap": cap is not None and len(known) > cap,
                "candidate_exceeds_cap": cap is not None and len(potential) > cap,
                "maximum_path_count": len(day_path),
                "path_exceeds_cap": cap is not None and len(day_path) > cap,
                "path_witness_match_ids": sorted(day_path),
                "known_matches": [describe(a) for a in known],
                "candidate_matches": [describe(a) for a in potential],
            }
        )
    # Rest never resets at midnight, even though the daily match counter does.
    for person in sorted({person for person, _ in possible}):
        for kind, mapping in (("known", fixed), ("candidate_upper_bound", possible)):
            rows = sorted(
                (a for (p, _), values in mapping.items() if p == person for a in values),
                key=lambda a: a.start_at,
            )
            for first, second in pairwise(rows):
                gap = (second.start_at - first.play_end_at).total_seconds() / 60
                required = rest_minutes(context, first.match_id, second.match_id)
                if first.court_id != second.court_id:
                    required += transfer_minutes(context, first.match_id, second.match_id)
                gaps.append(
                    {
                        "participant_id": person,
                        "name": people[person],
                        "date": second.start_at.astimezone(context.zone).date().isoformat(),
                        "basis": kind,
                        "gap_minutes": gap,
                        "required_minutes": required,
                        "violation": gap < required,
                        "first": describe(first),
                        "second": describe(second),
                    }
                )
    errors = [
        i.model_dump(mode="json")
        for i in validate_schedule(working)
        if i.severity == "error" and i.status == "open"
    ]
    return {
        "status": "violations" if errors else "passed",
        "source_revision_id": snapshot.id,
        "timezone": str(context.zone),
        "policy": {
            "minimum_rest_minutes": context.minimum_rest_minutes,
            "max_matches_per_day": context.max_matches_per_day,
        },
        "registrations": len(working.registrations),
        "participants": len(working.participants),
        "scheduled_matches": len(assigned),
        "daily": sorted(daily, key=lambda row: (-row["known_count"], row["name"], row["date"])),
        "gaps": sorted(gaps, key=lambda row: row["gap_minutes"]),
        "issues": errors,
        "path_analysis": list(context.path_analysis.reports.values())
        if context.path_analysis is not None
        else [],
        "path_strategy": "confirmed_only"
        if context.participant_scope == "confirmed_only"
        else "qualification_paths"
        if context.path_analysis is not None
        else "conservative",
        "notes": [
            "以參賽者身分跨組合併，日期採賽事時區。",
            "僅計入已確定參賽者；未確定晉級路徑不預留，晉級後須重新檢查。"
            if context.participant_scope == "confirmed_only"
            else "原始候選上界包含互斥路徑；maximum_path_count 才是保留相容資格決定的單日拓樸上界，尚未核定名次仍保留可能性。",
            "休息以預定打完時間至下一場開打計算，不以兩場開打時間相減。現場開賽另檢查實際紀錄。",
            "稽核不修改來源；上限未設定的舊工作區維持原行為。",
        ],
    }
