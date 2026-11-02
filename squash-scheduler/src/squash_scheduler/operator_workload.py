"""Read-only operator projection of scheduled per-person workload and compatible conflicts.

Unlike a raw candidate union, qualification paths preserve mutually exclusive
winner/loser, pool-rank and draw decisions. This module consumes the same path
and timing helpers as the independent schedule validator; it never picks winners
or changes a saved workload policy.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from itertools import combinations, pairwise
from typing import Any

from .domain.models import Snapshot, canonical_hash
from .domain.rankings import resolve_side
from .exporters.messages import issue_label
from .scheduling.constraints import build_context, rest_minutes, transfer_minutes, validate_schedule
from .scheduling.paths import PathAnalysisIncomplete, analyze_paths

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
_LOAD_CODES = {"PLAYER_REST_VIOLATION", "PLAYER_DAILY_LIMIT_EXCEEDED"}


def build_operator_workload(snapshot: Snapshot, *, policy: dict | None = None) -> dict[str, Any]:
    """Project one snapshot; ``policy`` is an explicitly labelled, unsaved simulation.

    The caller may cache by workspace, revision/content hash and policy. No cache
    lives here, so a returned mutable projection cannot poison a later request.
    ``status`` describes workload of assigned active matches only; structural and
    court issues are separate, and unassigned matches are explicitly counted.
    """
    working = snapshot.model_copy(deep=True)
    configured = dict(snapshot.config.get("player_workload") or {})
    if policy is not None:
        working.config["player_workload"] = dict(policy)
    event = working.config.get("event") or {}
    dates = sorted(event.get("event_dates") or [])
    people = {p.id: p.display_name for p in working.participants}
    registrations = {r.id: r.participant_id for r in working.registrations}
    owners = {e.id: registrations[e.registration_id] for e in working.entries}
    divisions = {d.id: d.name for d in working.divisions}
    courts = {c["id"]: c["label"] for c in working.config.get("courts", [])}
    person_divisions: dict[str, set[str]] = defaultdict(set)
    for entry in working.entries:
        person_divisions[owners[entry.id]].add(entry.effective_division_id)
    for reg in working.registrations:
        if not any(e.registration_id == reg.id for e in working.entries):
            person_divisions[reg.participant_id].add(reg.original_division_id)
    report: dict[str, Any] = {
        "source_revision_id": snapshot.id,
        "timezone": event.get("timezone"),
        "event_dates": dates,
        "mode": "simulation" if policy is not None else "configured",
        "configured_policy": configured,
        "policy": dict(working.config.get("player_workload") or {}),
        "status": "invalid",
        "assessment_scope": "scheduled_matches",
        "schedule_complete": False,
        "counts": {
            "participants": len(people),
            "affected_participants": 0,
            "known_conflicts": 0,
            "possible_conflicts": 0,
            "scheduled_matches": 0,
            "unscheduled_matches": 0,
        },
        "participants": [],
        "conflicts": [],
        "global_issues": [],
        "issues": [],
        "path_strategy": "qualification_paths",
        "notes": [
            "以同一參賽者身分跨組合併，日期採賽事時區；只檢查已排定的有效場次。",
            "已確定場數是對手身分已確定的安排，不代表已完賽；可能場數為相容晉級路徑的單日最大值，不代表一定出賽。",
            "可能休息衝突僅列同一選手可同時參加的兩場，已排除決賽／季軍及池內名次等互斥分支。",
            "休息從預定打完至下一場開打計算；跨日仍檢查，跨場地另加既有換場時間。現場開賽另依實際紀錄驗證。",
            "試算不儲存政策或排程；負荷通過不代表場地、資格、未排場次或正式發布均已通過。",
        ],
    }
    context, issues = build_context(working)
    if context is None:
        report["issues"] = [i.model_dump(mode="json") for i in issues]
        return report
    # Older workspaces deliberately retain their saved policy. Still use exact
    # compatible paths for their dashboard, rather than labelling raw unions as play.
    if context.path_analysis is None:
        try:
            context.path_analysis = analyze_paths(working, set(context.matches))
        except (PathAnalysisIncomplete, KeyError, StopIteration, RecursionError) as exc:
            report["issues"] = [{"code": "PLAYER_PATH_ANALYSIS_INCOMPLETE", "message": str(exc)}]
            return report
    report["policy"] = {
        "minimum_rest_minutes": min(
            (
                max(context.minimum_rest_minutes, t["minimum_player_rest_minutes"])
                for t in context.timing.values()
            ),
            default=context.minimum_rest_minutes,
        ),
        "max_matches_per_day": context.max_matches_per_day,
    }
    report["timezone"] = str(context.zone)
    assigned = {a.match_id: a for a in working.assignments if a.match_id in context.matches}
    unassigned = context.matches.keys() - assigned.keys()
    all_dates = sorted(
        set(dates)
        | {a.start_at.astimezone(context.zone).date().isoformat() for a in assigned.values()}
    )
    report["event_dates"] = dates
    report["schedule_complete"] = not unassigned
    report["counts"].update(scheduled_matches=len(assigned), unscheduled_matches=len(unassigned))
    known_people = {
        mid: {
            owners[eid]
            for side in (context.matches[mid].side_a, context.matches[mid].side_b)
            if (eid := resolve_side(working, side)) is not None and eid in owners
        }
        for mid in assigned
    }
    known: dict[str, list] = defaultdict(list)
    possible: dict[str, list] = defaultdict(list)
    for mid, assignment in assigned.items():
        for person in known_people[mid]:
            known[person].append(assignment)
        for person in context.candidates.get(mid, set()):
            possible[person].append(assignment)

    def describe(assignment):
        match = context.matches[assignment.match_id]
        return {
            "match_id": match.id,
            "code": match.display_code or match.id[:8],
            "division_id": match.division_id,
            "division": divisions.get(match.division_id, "未指定組別"),
            "status": match.status,
            "start": assignment.start_at.astimezone(context.zone).isoformat(),
            "play_end": assignment.play_end_at.astimezone(context.zone).isoformat(),
            "court_id": assignment.court_id,
            "court": courts.get(assignment.court_id, "未知場地"),
        }

    def interval(first, second, participant_context):
        gap = (second.start_at - first.play_end_at).total_seconds() / 60
        required = rest_minutes(participant_context, first.match_id, second.match_id)
        if first.court_id != second.court_id:
            required += transfer_minutes(context, first.match_id, second.match_id)
        return {
            "date": second.start_at.astimezone(context.zone).date().isoformat(),
            "gap_minutes": gap,
            "required_minutes": required,
            "violation": gap < required,
            "first": describe(first),
            "second": describe(second),
        }

    paths = context.player_paths()
    conflicts = []
    for person in sorted(people, key=lambda pid: (people[pid], pid)):
        # The solver's pair-wide wait protects every possible participant. A
        # person's row must not inherit a different candidate's longer override.
        participant_context = replace(
            context, participant_rest={person: context.participant_rest.get(person, 0)}
        )
        known_rows = sorted(known[person], key=lambda a: (a.start_at, a.match_id))
        possible_rows = sorted(possible[person], key=lambda a: (a.start_at, a.match_id))
        person_row: dict[str, Any] = {
            "participant_id": person,
            "name": people[person],
            "divisions": [
                {"id": did, "name": divisions.get(did, "未指定組別")}
                for did in sorted(person_divisions[person], key=lambda did: divisions.get(did, did))
            ],
            "daily": [],
            "known_gaps": [
                {"basis": "known", **interval(a, b, participant_context)}
                for a, b in pairwise(known_rows)
            ],
            "conflicts": [],
            "conflict_count": 0,
            "unassigned_match_ids": sorted(
                mid for mid in unassigned if person in context.candidates.get(mid, set())
            ),
        }

        def add_conflict(
            code, basis, mids, day, detail, person=person, person_row=person_row, **extra
        ):
            identity = {"participant_id": person, "code": code, "date": day, "match_ids": mids}
            conflict = {
                "id": canonical_hash(identity),
                "code": code,
                "label": "每日場數超額"
                if code == "PLAYER_DAILY_LIMIT_EXCEEDED"
                else "休息不足／出賽重疊",
                "detail": detail,
                "participant_ids": [person],
                "participant_names": [people[person]],
                "match_ids": mids,
                "date": day,
                "basis": basis,
                "gap_minutes": None,
                "required_minutes": None,
                "first": None,
                "second": None,
                **extra,
            }
            person_row["conflicts"].append(conflict)
            conflicts.append(conflict)

        for day in all_dates:
            known_day = [
                a
                for a in known_rows
                if a.start_at.astimezone(context.zone).date().isoformat() == day
            ]
            possible_day = [
                a
                for a in possible_rows
                if a.start_at.astimezone(context.zone).date().isoformat() == day
            ]
            day_ids = {a.match_id for a in possible_day}
            witness = max(
                (path & day_ids for path in paths.get(person, [])),
                key=lambda path: (len(path), sorted(path)),
                default=frozenset(),
            )
            cap = context.max_matches_per_day
            known_exceeds = cap is not None and len(known_day) > cap
            path_exceeds = cap is not None and len(witness) > cap
            person_row["daily"].append(
                {
                    "date": day,
                    "known_count": len(known_day),
                    "maximum_path_count": len(witness),
                    "cap_exceeded": known_exceeds or path_exceeds,
                    "known_exceeds_cap": known_exceeds,
                    "path_exceeds_cap": path_exceeds,
                    "known_matches": [describe(a) for a in known_day],
                    "candidate_matches": [describe(a) for a in possible_day],
                    "path_witness_match_ids": sorted(witness),
                }
            )
            if known_exceeds or path_exceeds:
                count = len(known_day) if known_exceeds else len(witness)
                basis = "known" if known_exceeds else "possible"
                mids = [a.match_id for a in known_day] if known_exceeds else sorted(witness)
                detail = f"{people[person]}在 {day} {'已確定安排' if known_exceeds else '相容晉級路徑最多'} {count} 場，上限 {cap} 場。"
                add_conflict("PLAYER_DAILY_LIMIT_EXCEEDED", basis, mids, day, detail)
        # The verifier checks every compatible pair, not adjacent rows in the raw
        # candidate union: an exclusive branch can sit between two real conflicts.
        for first, second in combinations(possible_rows, 2):
            if person not in context.shared_people(first.match_id, second.match_id):
                continue
            gap = interval(first, second, participant_context)
            if not gap["violation"]:
                continue
            basis = (
                "known"
                if person in known_people[first.match_id] & known_people[second.match_id]
                else "possible"
            )
            detail = f"{people[person]}：第 {gap['first']['code']} 場 → 第 {gap['second']['code']} 場，預定休息 {gap['gap_minutes']:g} 分鐘，至少需要 {gap['required_minutes']} 分鐘。"
            add_conflict(
                "PLAYER_REST_VIOLATION",
                basis,
                [first.match_id, second.match_id],
                gap["date"],
                detail,
                **{key: value for key, value in gap.items() if key != "date"},
            )
        person_row["conflict_count"] = len(person_row["conflicts"])
        report["participants"].append(person_row)

    # Keep non-workload validator findings separate. A clear load dashboard is
    # never a claim that the complete schedule is valid or ready to publish.
    labels = {
        **people,
        **courts,
        **{mid: f"第 {m.display_code or mid[:8]} 場" for mid, m in context.matches.items()},
    }
    for item in validate_schedule(working):
        if item.code in _LOAD_CODES or item.severity != "error" or item.status != "open":
            continue
        detail = item.message
        for identifier in _UUID.findall(detail):
            detail = detail.replace(identifier, labels.get(identifier, "未確認資料"))
        identifiers = set(_UUID.findall(item.message))
        if item.entity_id is not None:
            identifiers.add(item.entity_id)
        report["global_issues"].append(
            {
                **item.model_dump(mode="json"),
                "label": issue_label(item.code),
                "detail": detail,
                "match_ids": sorted(identifiers & context.matches.keys()),
            }
        )
    report["conflicts"] = conflicts
    report["counts"].update(
        affected_participants=sum(bool(row["conflicts"]) for row in report["participants"]),
        known_conflicts=sum(c["basis"] == "known" for c in conflicts),
        possible_conflicts=sum(c["basis"] == "possible" for c in conflicts),
    )
    report["status"] = "violations" if conflicts else "passed"
    return report
