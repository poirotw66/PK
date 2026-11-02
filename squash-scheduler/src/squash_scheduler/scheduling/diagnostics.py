"""Necessary-condition diagnostics, never a claim of real-event impossibility."""

from __future__ import annotations

from typing import Any

from squash_scheduler.domain.matches import dependency_map, possible_participants
from squash_scheduler.domain.models import Assignment, Snapshot

from .constraints import ScheduleContext, issue, timestamp


def impacted_match_ids(snapshot: Snapshot, changed_match_ids: set[str]) -> set[str]:
    """Close a proposed local scope over qualification, people and occupied courts.

    Court edges require overlapping occupation; merely sharing a court on the
    same day does not force every match into the initial scope. On failure the
    separate expansion proposal may suggest that wider scope for review.
    """
    dependencies = dependency_map(snapshot)
    candidates = possible_participants(snapshot)
    assignments = {a.match_id: a for a in snapshot.assignments}
    affected = set(changed_match_ids)
    while True:
        expanded = set(affected)
        people = set().union(*(candidates.get(m, set()) for m in affected)) if affected else set()
        for match_id in dependencies:
            if (
                candidates.get(match_id, set()) & people
                or dependencies.get(match_id, set()) & affected
                or match_id in affected
            ):
                expanded.add(match_id)
                expanded.update(dependencies.get(match_id, set()))
            prior = assignments.get(match_id)
            if prior is not None and any(
                current.court_id == prior.court_id
                and current.start_at < prior.court_release_at
                and prior.start_at < current.court_release_at
                for seed in affected
                if (current := assignments.get(seed)) is not None
            ):
                expanded.add(match_id)
        if expanded == affected:
            return affected
        affected = expanded


def model_diagnostics(context: ScheduleContext) -> dict[str, Any]:
    return {
        "candidate_conflict_strategy": "qualification_paths"
        if context.path_analysis is not None
        else "conservative",
        "interpretation": (
            "Every compatible single-person topology path must fit; mutually exclusive branches are not summed. Unresolved pool rankings remain possible."
            if context.path_analysis is not None
            else "Candidate sets are upper bounds. INFEASIBLE proves this conservative model infeasible; it does not prove that actual qualifiers cannot be scheduled."
        ),
        "match_ids": sorted(context.matches),
        "court_ids": list(context.windows),
        "constraints": [
            "one_assignment",
            "court_occupation_including_buffer",
            "court_availability",
            "daily_start_grid",
            "possible_participant_rest_and_transfer",
            "possible_participant_daily_limit",
            "advancement_and_ranking_confirmation",
            "locks_and_started_matches",
        ],
        "modifiable_parameters": [
            "court availability",
            "event dates",
            "daily grid anchors",
            "estimated play minutes",
            "court buffer minutes",
            "minimum player rest minutes",
            "event-wide player rest floor and daily match cap",
            "court transfer minutes",
            "ranking confirmation minutes",
            "explicit manual locks",
        ],
        "required_court_minutes": sum(
            t["estimated_play_minutes"] + t["court_buffer_minutes"] for t in context.timing.values()
        ),
        "available_court_minutes": sum(
            b - a for windows in context.windows.values() for a, b in windows
        ),
        "timing_by_match": context.timing,
        "qualification_paths": list(context.path_analysis.reports.values())
        if context.path_analysis is not None
        else [],
        "player_workload": {
            "minimum_rest_minutes": context.minimum_rest_minutes,
            "max_matches_per_day": context.max_matches_per_day,
            "counting": "confirmed_only"
            if context.participant_scope == "confirmed_only"
            else "qualification_paths"
            if context.path_analysis is not None
            else "conservative_candidate_upper_bound",
        },
        "objective": {
            "priority": ["minimize changed existing assignments", "minimize latest court release"],
            "method": "weighted exact lexicographic objective; change_weight = horizon_span + 1",
            "promise": "Feasibility only; no cross-runtime schedule reproducibility guarantee",
        },
    }


def preflight(context: ScheduleContext, choices: dict[str, list[tuple[int, int]]]) -> list:
    diagnostics = model_diagnostics(context)
    issues = []
    if diagnostics["required_court_minutes"] > diagnostics["available_court_minutes"]:
        issues.append(
            issue(
                "COURT_CAPACITY_INSUFFICIENT",
                "Required court minutes exceed legal availability",
                suggested_action="Propose additional availability or organizer-approved rules",
            )
        )
    for match_id, values in choices.items():
        if not values:
            issues.append(
                issue(
                    "MATCH_NO_LEGAL_SLOT",
                    "No court window and grid slot fits this match and buffer",
                    match_id,
                    suggested_action="Review court windows, grid and timing rules",
                )
            )
    if context.max_matches_per_day is not None:
        match_days = {
            m: {timestamp(start).astimezone(context.zone).date() for start, _ in values}
            for m, values in choices.items()
        }
        for person, paths in sorted(context.player_paths().items()):
            violating = []
            for path in paths:
                days = set().union(*(match_days[m] for m in path)) if path else set()
                if len(path) > context.max_matches_per_day * len(days):
                    violating.append((len(path), sorted(path), days))
            if violating:
                count, witness, days = max(violating, key=lambda item: item[0])
                issues.append(
                    issue(
                        "PLAYER_DAILY_CAPACITY_INSUFFICIENT",
                        f"Qualification path for {person}: {count} matches exceed {len(days)} dates x {context.max_matches_per_day}; witness={witness}",
                        field_path=f"participants.{person}",
                        suggested_action="Fixed dates and daily cap cannot cover this path; review registrations or approved format, never alter dates or cap automatically",
                    )
                )
    return issues


def expansion_proposal(
    snapshot: Snapshot, context: ScheduleContext, local_match_ids: set[str]
) -> dict[str, Any]:
    """Propose a wider review scope; do not retry or unlock automatically."""
    proposed = set(local_match_ids)
    affected_people = (
        set().union(*(context.candidates.get(m, set()) for m in proposed)) if proposed else set()
    )
    current = {a.match_id: a for a in snapshot.assignments}
    affected_courts = {current[m].court_id for m in proposed if m in current}
    for match_id in context.matches:
        if (
            context.candidates.get(match_id, set()) & affected_people
            or context.dependencies.get(match_id, set()) & local_match_ids
            or context.dependencies.get(match_id, set()) & proposed
            or (match_id in current and current[match_id].court_id in affected_courts)
        ):
            proposed.add(match_id)
    return {
        "status": "proposal_only",
        "requires_review": True,
        "original_match_ids": sorted(local_match_ids),
        "proposed_match_ids": sorted(proposed),
        "additional_match_ids": sorted(proposed - local_match_ids),
        "locks_preserved": True,
        "reason": "Local solve could not produce a validated schedule",
    }


def schedule_diff(before: list[Assignment], after: list[Assignment]) -> list[dict[str, Any]]:
    original = {a.match_id: a for a in before}
    changes = []
    for assignment in after:
        prior = original.get(assignment.match_id)
        if prior is None or prior != assignment:
            changes.append(
                {
                    "match_id": assignment.match_id,
                    "before": prior.model_dump(mode="json") if prior else None,
                    "after": assignment.model_dump(mode="json"),
                    "reason": "Constraint-checked scheduling draft",
                }
            )
    return changes
