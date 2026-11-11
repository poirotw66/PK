"""Pure timing expansion and independent schedule verification.

All interval arithmetic uses UTC whole minutes. Local dates and daily anchors
are interpreted in the event timezone. A buffer belongs to the court, never to
the player's rest calculation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from itertools import combinations, pairwise
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from squash_scheduler.domain.config import PlayerWorkload
from squash_scheduler.domain.matches import (
    dependency_map,
    possible_participants,
    validate_graph,
)
from squash_scheduler.domain.models import Assignment, Issue, LoserSide, Match, Snapshot, WinnerSide

from .paths import PathAnalysis, PathAnalysisIncomplete, analyze_paths

MINUTE = timedelta(minutes=1)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TIMING_KEYS = (
    "estimated_play_minutes",
    "court_buffer_minutes",
    "minimum_player_rest_minutes",
    "court_transfer_minutes",
    "ranking_confirmation_minutes",
)


def issue(
    code: str,
    message: str,
    entity_id: str | None = None,
    field_path: str = "",
    suggested_action: str = "",
) -> Issue:
    return Issue(
        code=code,
        message=message,
        gate="G4",
        entity_type="match" if entity_id else "schedule",
        entity_id=entity_id,
        field_path=field_path,
        suggested_action=suggested_action,
    )


def minute(dt: datetime) -> int:
    return int((dt.astimezone(UTC) - EPOCH) // MINUTE)


def timestamp(value: int) -> datetime:
    return EPOCH + value * MINUTE


def active_matches(snapshot: Snapshot) -> dict[str, Match]:
    return {
        m.id: m
        for m in snapshot.matches
        if m.status not in {"cancelled", "excluded"}
        and m.side_a.type != "bye"
        and m.side_b.type != "bye"
    }


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _subtract(
    windows: list[tuple[int, int]], blackouts: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    for blocked_start, blocked_end in _merge(blackouts):
        updated = []
        for start, end in windows:
            if blocked_end <= start or end <= blocked_start:
                updated.append((start, end))
            else:
                if start < blocked_start:
                    updated.append((start, blocked_start))
                if blocked_end < end:
                    updated.append((blocked_end, end))
        windows = updated
    return windows


def _interval(raw: Any) -> tuple[int, int]:
    start = datetime.fromisoformat(raw["start_at"])
    end = datetime.fromisoformat(raw["end_at"])
    if any(v.utcoffset() is None or v.second or v.microsecond for v in (start, end)):
        raise ValueError("Intervals require timezone-aware whole-minute timestamps")
    if start >= end:
        raise ValueError("Interval start must precede end")
    return minute(start), minute(end)


def effective_timing(snapshot: Snapshot, match: Match, *, frozen: bool = True) -> dict[str, Any]:
    if frozen and match.rule_snapshot is not None:
        return dict(match.rule_snapshot["timing"])
    values = dict(snapshot.config.get("timing_defaults") or {})
    division = next((d for d in snapshot.divisions if d.id == match.division_id), None)
    if division is not None:
        values.update({k: v for k, v in division.rule_overrides.items() if k in TIMING_KEYS})
        values.update(division.rule_overrides.get("timing") or {})
    stage = next((s for s in snapshot.stages if s.id == match.stage_id), None)
    rule_sets = snapshot.config.get("rule_sets") or {}
    if isinstance(rule_sets, list):
        rule_sets = {r.get("id"): r for r in rule_sets}
    if stage is not None and stage.rule_set_id:
        rules = rule_sets.get(stage.rule_set_id, {})
        values.update({k: v for k, v in rules.items() if k in TIMING_KEYS})
        values.update(rules.get("timing") or {})
    return values


@dataclass
class ScheduleContext:
    matches: dict[str, Match]
    timing: dict[str, dict[str, int]]
    windows: dict[str, list[tuple[int, int]]]
    grid: int
    anchors: dict[date, int]
    zone: ZoneInfo
    candidates: dict[str, set[str]]
    dependencies: dict[str, set[str]]
    participant_rest: dict[str, int]
    minimum_rest_minutes: int = 0
    max_matches_per_day: int | None = None
    path_analysis: PathAnalysis | None = None
    deferred_ready: dict[str, int] = field(default_factory=dict)
    deferred_lags: dict[str, dict[str, int]] = field(default_factory=dict)
    participant_scope: str = "all_possible"

    def shared_people(self, first: str, second: str) -> set[str]:
        if self.path_analysis is not None:
            return self.path_analysis.shared(first, second)
        return self.candidates.get(first, set()) & self.candidates.get(second, set())

    def advances_from(self, match_id: str, prior_id: str) -> bool:
        match = self.matches[match_id]
        return any(
            isinstance(side, (WinnerSide, LoserSide)) and side.match_id == prior_id
            for side in (match.side_a, match.side_b)
        )

    def requires_player_rest(self, first: str, second: str) -> bool:
        return (
            bool(self.shared_people(first, second))
            or bool(self.minimum_rest_minutes)
            and (self.advances_from(first, second) or self.advances_from(second, first))
        )

    def player_paths(self) -> dict[str, list[frozenset[str]]]:
        if self.path_analysis is not None:
            return self.path_analysis.paths
        people = (
            set().union(*(self.candidates.get(mid, set()) for mid in self.matches))
            if self.matches
            else set()
        )
        return {
            person: [
                frozenset(mid for mid in self.matches if person in self.candidates.get(mid, set()))
            ]
            for person in people
        }

    def choices(self, match_id: str) -> list[tuple[int, int]]:
        """Return (start minute, court index), including buffers in availability."""
        duration = (
            self.timing[match_id]["estimated_play_minutes"]
            + self.timing[match_id]["court_buffer_minutes"]
        )
        result: list[tuple[int, int]] = []
        for court_index, windows in enumerate(self.windows.values()):
            for start, end in windows:
                day = timestamp(start).astimezone(self.zone).date()
                anchor = self.anchors[day]
                first = anchor + max(0, (start - anchor + self.grid - 1) // self.grid) * self.grid
                result.extend(
                    (value, court_index)
                    for value in range(first, end - duration + 1, self.grid)
                    if value >= self.deferred_ready.get(match_id, value)
                )
        return sorted(set(result))


def build_context(snapshot: Snapshot) -> tuple[ScheduleContext | None, list[Issue]]:
    """Reject unknown input before creating a constraint model."""
    graph_issues = validate_graph(snapshot)
    if any(i.severity == "error" and i.status == "open" for i in graph_issues):
        return None, graph_issues
    issues = list(graph_issues)
    config = snapshot.config
    defaults = config.get("timing_defaults") or {}
    event = config.get("event") or {}
    matches = active_matches(snapshot)
    try:
        workload = PlayerWorkload.model_validate(config.get("player_workload", {}))
    except ValueError as exc:
        return None, [
            issue("PLAYER_WORKLOAD_CONFIG_INVALID", str(exc), field_path="config.player_workload")
        ]
    try:
        if not isinstance(defaults, dict) or not isinstance(event, dict):
            raise TypeError("event and timing_defaults must be configuration objects")
        if not event.get("timezone") or not event.get("event_dates"):
            raise ValueError("event.timezone and event.event_dates must be configured")
        zone = ZoneInfo(event["timezone"])
        dates = [date.fromisoformat(value) for value in event["event_dates"]]
        if len(dates) != len(set(dates)):
            raise ValueError("event.event_dates must be unique")
        grid = defaults.get("start_grid_minutes")
        if type(grid) is not int or grid <= 0:
            raise ValueError("start_grid_minutes must be a configured positive integer")
        raw_anchors = defaults.get("daily_grid_anchors")
        if not isinstance(raw_anchors, list) or not raw_anchors:
            raise ValueError("daily_grid_anchors must contain one anchor per event date")
        anchors = {}
        for anchor in raw_anchors:
            day = date.fromisoformat(anchor["date"])
            local = time.fromisoformat(anchor["local_time"])
            if local.tzinfo is not None or local.second or local.microsecond:
                raise ValueError("Grid anchors require local whole-minute times")
            if day in anchors:
                raise ValueError("Duplicate daily grid anchor")
            local_dt = datetime.combine(day, local, zone)
            if local_dt.astimezone(UTC).astimezone(zone) != local_dt:
                raise ValueError("Daily grid anchor is not a real local time")
            if local_dt.replace(fold=0).utcoffset() != local_dt.replace(fold=1).utcoffset():
                raise ValueError("Daily grid anchor is ambiguous in the event timezone")
            anchors[day] = minute(local_dt)
        if set(anchors) != set(dates):
            raise ValueError("Exactly one anchor is required for each event date")
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError) as exc:
        return None, [
            *issues,
            issue("TIMING_CONFIG_MISSING", str(exc), field_path="config.timing_defaults"),
        ]
    timing = {}
    rule_sets = config.get("rule_sets") or {}
    if not isinstance(rule_sets, (dict, list)) or (
        isinstance(rule_sets, list) and not all(isinstance(r, dict) for r in rule_sets)
    ):
        return None, [*issues, issue("TIMING_CONFIG_MISSING", "Invalid stage rule sets")]
    for match in matches.values():
        try:
            values = effective_timing(snapshot, match)
        except (ValueError, TypeError, AttributeError) as exc:
            issues.append(
                issue("TIMING_CONFIG_MISSING", f"Invalid timing override: {exc}", match.id)
            )
            values = {}
        for key in TIMING_KEYS:
            value = values.get(key)
            if type(value) is not int or value < (1 if key == "estimated_play_minutes" else 0):
                issues.append(
                    issue(
                        "TIMING_CONFIG_MISSING",
                        f"{key} must be explicitly configured",
                        match.id,
                        f"timing.{key}",
                    )
                )
        timing[match.id] = values
        stage = next((s for s in snapshot.stages if s.id == match.stage_id), None)
        rule_ids = (
            set(rule_sets) if isinstance(rule_sets, dict) else {r.get("id") for r in rule_sets}
        )
        if stage and stage.rule_set_id and stage.rule_set_id not in rule_ids:
            issues.append(
                issue(
                    "TIMING_CONFIG_MISSING",
                    "Referenced stage rule set is missing",
                    match.id,
                    "stage.rule_set_id",
                )
            )
    windows = {}
    courts = config.get("courts")
    if not isinstance(courts, list) or not courts:
        issues.append(
            issue(
                "TIMING_CONFIG_MISSING",
                "Court availability must be configured",
                field_path="config.courts",
            )
        )
    for court in courts or []:
        try:
            court_id = str(court["id"])
            UUID(court_id)
            if court_id in windows:
                raise ValueError("Duplicate court ID")
            if not isinstance(court.get("availability"), list) or not court["availability"]:
                raise ValueError("Court availability must be explicitly configured")
            if not isinstance(court.get("blackouts"), list):
                raise TypeError("Court blackouts must be an explicit list (empty is allowed)")
            available = _merge([_interval(v) for v in court["availability"]])
            blackouts = [_interval(v) for v in court["blackouts"]]
            available = _subtract(available, blackouts)
            clipped: list[tuple[int, int]] = []
            for day in dates:
                day_start = minute(datetime.combine(day, time.min, zone))
                day_end = minute(datetime.combine(day + timedelta(days=1), time.min, zone))
                clipped.extend(
                    (max(a, day_start), min(b, day_end))
                    for a, b in available
                    if max(a, day_start) < min(b, day_end)
                )
            windows[court_id] = sorted(clipped)
        except (KeyError, ValueError, TypeError) as exc:
            issues.append(issue("TIMING_CONFIG_MISSING", str(exc), field_path="config.courts"))
    candidates = possible_participants(snapshot)
    if workload.participant_scope == "confirmed_only":
        from squash_scheduler.domain.rankings import resolve_side

        registrations = {r.id: r.participant_id for r in snapshot.registrations}
        entries = {e.id: registrations[e.registration_id] for e in snapshot.entries}
        candidates = {
            match.id: {
                entries[entry_id]
                for side in (match.side_a, match.side_b)
                if (entry_id := resolve_side(snapshot, side)) is not None and entry_id in entries
            }
            for match in matches.values()
        }
    all_people = set().union(*(candidates.get(m, set()) for m in matches)) if matches else set()
    people = {p.id: p for p in snapshot.participants}
    for person_id in all_people:
        if person_id not in people or people[person_id].identity_status != "confirmed":
            issues.append(
                issue(
                    "IDENTITY_AMBIGUOUS",
                    "A possible participant identity is unresolved",
                    field_path=f"participants.{person_id}",
                )
            )
    if any(i.severity == "error" and i.status == "open" for i in issues):
        return None, issues
    deferred_ready, deferred_lags = deferred_timing(snapshot)
    path_analysis = None
    if workload.participant_scope != "confirmed_only" and (
        workload.max_matches_per_day is not None or workload.minimum_rest_minutes is not None
    ):
        try:
            path_analysis = analyze_paths(snapshot, set(matches))
        except (PathAnalysisIncomplete, KeyError, StopIteration, RecursionError) as exc:
            return None, [*issues, issue("PLAYER_PATH_ANALYSIS_INCOMPLETE", str(exc))]
    dependencies = dependency_map(snapshot)
    if (config.get("runtime") or {}).get("keep_elimination_rounds_in_order"):
        elimination_stages = {stage.id for stage in snapshot.stages if stage.type == "elimination"}
        by_stage: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
        for match in matches.values():
            if match.stage_id in elimination_stages and match.kind != "exhibition":
                by_stage[match.stage_id][match.round].add(match.id)
        for rounds in by_stage.values():
            for before, after in pairwise(sorted(rounds)):
                for mid in rounds[after]:
                    dependencies.setdefault(mid, set()).update(rounds[before])
    return ScheduleContext(
        matches,
        timing,
        windows,
        grid,
        anchors,
        zone,
        candidates,
        dependencies,
        {p.id: p.minimum_rest_minutes or 0 for p in snapshot.participants},
        minimum_rest_minutes=workload.minimum_rest_minutes or 0,
        max_matches_per_day=workload.max_matches_per_day,
        path_analysis=path_analysis,
        deferred_ready=deferred_ready,
        deferred_lags=deferred_lags,
        participant_scope=workload.participant_scope,
    ), issues


def deferred_timing(snapshot: Snapshot) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    from squash_scheduler.domain.deferred_draws import slot_timing
    from squash_scheduler.domain.models import DrawSlotSide

    draws = {d["id"]: d for d in snapshot.draws}
    ready: dict[str, int] = {}
    lags: dict[str, dict[str, int]] = {}
    for match in snapshot.matches:
        for side in (match.side_a, match.side_b):
            if (
                not isinstance(side, DrawSlotSide)
                or draws.get(side.draw_run_id, {}).get("kind") != "deferred_slots"
            ):
                continue
            bound, source_lags = slot_timing(snapshot, side)
            if bound is not None:
                # Round up: a draw completing mid-minute cannot permit an earlier start.
                value = minute(bound) + int(bool(bound.second or bound.microsecond))
                ready[match.id] = max(ready.get(match.id, value), value)
            for prior, lag in source_lags.items():
                lags.setdefault(match.id, {})[prior] = max(
                    lags.get(match.id, {}).get(prior, 0), lag
                )
    return ready, lags


def rest_minutes(context: ScheduleContext, first: str, second: str) -> int:
    shared = context.shared_people(first, second)
    return max(
        context.minimum_rest_minutes,
        context.timing[first]["minimum_player_rest_minutes"],
        context.timing[second]["minimum_player_rest_minutes"],
        max((context.participant_rest.get(p, 0) for p in shared), default=0),
    )


def transfer_minutes(context: ScheduleContext, first: str, second: str) -> int:
    return max(
        context.timing[first]["court_transfer_minutes"],
        context.timing[second]["court_transfer_minutes"],
    )


def ranking_dependencies(snapshot: Snapshot, context: ScheduleContext) -> dict[str, set[str]]:
    """Ranking confirmation runs concurrently with player rest after pool games."""
    pools = {p.id: set(p.entry_ids) for p in snapshot.pools}
    output: dict[str, set[str]] = {m: set() for m in context.matches}
    for match in context.matches.values():
        for side in (match.side_a, match.side_b):
            if side.type == "pool_rank":
                entries = pools[side.pool_id]
                for prior_id in context.dependencies.get(match.id, set()):
                    prior = context.matches.get(prior_id)
                    if prior and any(
                        s.type == "entry" and s.entry_id in entries
                        for s in (prior.side_a, prior.side_b)
                    ):
                        output[match.id].add(prior_id)
    return output


def is_time_locked(assignment: Assignment) -> bool:
    return bool(assignment.locks.get("time") or assignment.locks.get("start_at"))


def is_court_locked(assignment: Assignment) -> bool:
    return bool(assignment.locks.get("court") or assignment.locks.get("court_id"))


def round_robin_match_groups(snapshot: Snapshot, matches: dict[str, Match]) -> list[list[str]]:
    """Return active round-robin groups, including unpartitioned divisions."""
    groups: list[list[str]] = []
    for stage in snapshot.stages:
        if stage.type != "round_robin":
            continue
        mids = [mid for mid, match in matches.items() if match.stage_id == stage.id]
        pools = [pool for pool in snapshot.pools if pool.stage_id == stage.id]
        stage_groups = (
            [
                [
                    mid
                    for mid in mids
                    if all(
                        getattr(side, "entry_id", None) in pool.entry_ids
                        for side in (matches[mid].side_a, matches[mid].side_b)
                    )
                ]
                for pool in pools
            ]
            if pools
            else [mids]
        )
        groups.extend(group for group in stage_groups if group)
    return groups


def validate_schedule(snapshot: Snapshot, *, baseline_assignments: Any = None) -> list[Issue]:
    """Recompute all constraints from the candidate snapshot, without solver state."""
    context, issues = build_context(snapshot)
    if context is None:
        return issues
    assignments: dict[str, Assignment] = {}
    for assignment in snapshot.assignments:
        if assignment.match_id in assignments:
            issues.append(
                issue("ASSIGNMENT_DUPLICATE", "Match has multiple assignments", assignment.match_id)
            )
        assignments[assignment.match_id] = assignment
        if assignment.match_id not in context.matches:
            issues.append(
                issue(
                    "ASSIGNMENT_INACTIVE",
                    "Inactive, bye or unknown match occupies a court",
                    assignment.match_id,
                )
            )
    for match_id in context.matches:
        if match_id not in assignments:
            issues.append(
                issue("ASSIGNMENT_MISSING", "Every active match needs one assignment", match_id)
            )
    valid = {m: a for m, a in assignments.items() if m in context.matches}
    for match_id, assignment in valid.items():
        start, end, release = map(
            minute, (assignment.start_at, assignment.play_end_at, assignment.court_release_at)
        )
        timing = context.timing[match_id]
        if end - start != timing["estimated_play_minutes"] or (
            release - end != timing["court_buffer_minutes"]
        ):
            issues.append(
                issue(
                    "ASSIGNMENT_DURATION",
                    "Play duration or court buffer differs from rules",
                    match_id,
                )
            )
        day = assignment.start_at.astimezone(context.zone).date()
        anchor = context.anchors.get(day)
        if anchor is None or start < anchor or (start - anchor) % context.grid:
            issues.append(
                issue(
                    "START_GRID_VIOLATION",
                    "Start is outside the event dates or daily grid",
                    match_id,
                )
            )
        if not any(
            a <= start and release <= b for a, b in context.windows.get(assignment.court_id, [])
        ):
            issues.append(
                issue(
                    "COURT_UNAVAILABLE", "Court occupation crosses a closure or blackout", match_id
                )
            )
    for (first_id, first), (second_id, second) in combinations(valid.items(), 2):
        if first.start_at > second.start_at:
            first_id, second_id, first, second = second_id, first_id, second, first
        if first.court_id == second.court_id and second.start_at < first.court_release_at:
            issues.append(issue("COURT_OVERLAP", f"Court overlaps match {first_id}", second_id))
        if context.requires_player_rest(first_id, second_id):
            wait = rest_minutes(context, first_id, second_id)
            if first.court_id != second.court_id:
                wait += transfer_minutes(context, first_id, second_id)
            if second.start_at < first.play_end_at + wait * MINUTE:
                issues.append(
                    issue(
                        "PLAYER_REST_VIOLATION",
                        f"Player rest is insufficient after {first_id}: {sorted(context.shared_people(first_id, second_id))}",
                        second_id,
                    )
                )
    if context.max_matches_per_day is not None:
        for person, paths in context.player_paths().items():
            worst: dict[date, list[str]] = {}
            for path in paths:
                counts: dict[date, list[str]] = defaultdict(list)
                for mid in sorted(path & valid.keys()):
                    counts[valid[mid].start_at.astimezone(context.zone).date()].append(mid)
                for day, mids in counts.items():
                    if len(mids) > len(worst.get(day, [])):
                        worst[day] = mids
            for day, match_ids in sorted(worst.items()):
                if len(match_ids) <= context.max_matches_per_day:
                    continue
                issues.append(
                    issue(
                        "PLAYER_DAILY_LIMIT_EXCEEDED",
                        f"Qualification path load: participant {person} on {day}: {len(match_ids)} matches exceed {context.max_matches_per_day}; matches={sorted(match_ids)}",
                        field_path=f"participants.{person}",
                        suggested_action="Replan within configured dates; witness contains compatible qualification branches",
                    )
                )
    rank_deps = ranking_dependencies(snapshot, context)
    for match_id, assignment in valid.items():
        if minute(assignment.start_at) < context.deferred_ready.get(
            match_id, minute(assignment.start_at)
        ):
            issues.append(
                issue(
                    "DEFERRED_DRAW_NOT_READY", "Deferred draw procedure has not finished", match_id
                )
            )
        for prior_id in context.dependencies.get(match_id, set()):
            prior = valid.get(prior_id)
            if prior is None:
                issues.append(
                    issue("ADVANCEMENT_NOT_READY", "A dependency has no assignment", match_id)
                )
                continue
            lag = (
                max(
                    context.timing[match_id]["ranking_confirmation_minutes"],
                    context.timing[prior_id]["ranking_confirmation_minutes"],
                )
                if prior_id in rank_deps[match_id]
                else 0
            )
            lag = max(lag, context.deferred_lags.get(match_id, {}).get(prior_id, 0))
            if assignment.start_at < prior.play_end_at + lag * MINUTE:
                issues.append(
                    issue(
                        "ADVANCEMENT_NOT_READY",
                        f"Dependency {prior_id} is not complete and confirmed",
                        match_id,
                    )
                )
    if (snapshot.config.get("runtime") or {}).get("keep_round_robin_groups_same_day") is True:
        for group in round_robin_match_groups(snapshot, context.matches):
            dates = {
                valid[mid].start_at.astimezone(context.zone).date() for mid in group if mid in valid
            }
            if len(dates) > 1:
                issues.append(
                    issue(
                        "ROUND_ROBIN_GROUP_SPLIT",
                        "A round-robin group spans multiple event dates",
                        group[0],
                    )
                )
    if baseline_assignments is not None:
        baseline = (
            baseline_assignments.values()
            if isinstance(baseline_assignments, dict)
            else baseline_assignments
        )
        for raw in baseline:
            before = raw if isinstance(raw, Assignment) else Assignment.model_validate(raw)
            match = context.matches.get(before.match_id)
            after = valid.get(before.match_id)
            immutable = match is not None and match.status in {"in_progress", "completed"}
            if immutable or is_time_locked(before) or is_court_locked(before):
                if after is None:
                    issues.append(
                        issue("LOCK_VIOLATION", "A locked assignment was removed", before.match_id)
                    )
                    continue
                if (immutable or is_time_locked(before)) and (
                    before.start_at != after.start_at
                    or before.play_end_at != after.play_end_at
                    or before.court_release_at != after.court_release_at
                ):
                    issues.append(
                        issue(
                            "LOCK_VIOLATION",
                            "Locked or started match times changed",
                            before.match_id,
                        )
                    )
                if (immutable or is_court_locked(before)) and before.court_id != after.court_id:
                    issues.append(
                        issue(
                            "LOCK_VIOLATION",
                            "Locked or started match court changed",
                            before.match_id,
                        )
                    )
    return issues
