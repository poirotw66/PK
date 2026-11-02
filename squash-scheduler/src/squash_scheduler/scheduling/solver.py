"""OR-Tools CP-SAT model. Only independently verified incumbents are committed."""

from __future__ import annotations

import math
from bisect import bisect_left, insort
from collections import Counter
from datetime import datetime
from itertools import combinations
from typing import Any

from ortools.sat.python import cp_model

from squash_scheduler.domain.models import Assignment, Snapshot

from .constraints import (
    ScheduleContext,
    build_context,
    is_court_locked,
    is_time_locked,
    issue,
    minute,
    ranking_dependencies,
    rest_minutes,
    round_robin_match_groups,
    timestamp,
    transfer_minutes,
    validate_schedule,
)
from .diagnostics import expansion_proposal, model_diagnostics, preflight, schedule_diff


def _greedy_hint(
    snapshot: Snapshot, context: ScheduleContext, choices: dict[str, list[tuple[int, int]]]
) -> list[Assignment]:
    """Build an earliest legal warm start with court-gap filling, never a solver result.

    Failure to fit is harmless: CP-SAT still searches its original full domains.
    Existing schedules and local repairs use the normal model without this heuristic.
    """
    courts = list(context.windows)
    rank_deps = ranking_dependencies(snapshot, context)
    chosen: dict[str, tuple[int, int]] = {}
    court_occupancy: dict[int, list[tuple[int, int]]] = {c: [] for c in range(len(courts))}
    appearances: Counter[str] = Counter()
    daily_appearances: Counter[tuple[str, int, str]] = Counter()
    path_owners: dict[str, list[tuple[str, int]]] = {mid: [] for mid in context.matches}
    for person, paths in context.player_paths().items():
        for index, path in enumerate(paths):
            for mid in path:
                path_owners[mid].append((person, index))
    pending = list(context.matches)
    while pending:
        eligible = [m for m in pending if context.dependencies.get(m, set()) <= chosen.keys()]
        if not eligible:
            return []
        # Avoid synchronizing every small pool's rest gaps. Spreading first
        # appearances across independent pools fills resources before return visits.
        ready = min(
            eligible,
            key=lambda m: (
                not bool(context.candidates.get(m)),
                max((appearances[p] for p in context.candidates.get(m, set())), default=0),
                sum(appearances[p] for p in context.candidates.get(m, set())),
            ),
        )
        dependency_ready = max(
            (
                chosen[p][0]
                + context.timing[p]["estimated_play_minutes"]
                + max(
                    context.deferred_lags.get(ready, {}).get(p, 0),
                    (
                        max(
                            context.timing[ready]["ranking_confirmation_minutes"],
                            context.timing[p]["ranking_confirmation_minutes"],
                        )
                        if p in rank_deps[ready]
                        else 0
                    ),
                )
                for p in context.dependencies.get(ready, set())
            ),
            default=0,
        )
        shared = [p for p in chosen if context.shared_people(ready, p)]
        options = []
        for court in range(len(courts)):
            lower = dependency_ready
            for prior in shared:
                start, prior_court = chosen[prior]
                lower = max(
                    lower,
                    start
                    + context.timing[prior]["estimated_play_minutes"]
                    + rest_minutes(context, ready, prior)
                    + (transfer_minutes(context, ready, prior) if court != prior_court else 0),
                )
            available = [start for start, c in choices[ready] if c == court]
            index = bisect_left(available, lower)
            occupation = (
                context.timing[ready]["estimated_play_minutes"]
                + context.timing[ready]["court_buffer_minutes"]
            )
            while index < len(available):
                proposed = available[index]
                day = timestamp(proposed).astimezone(context.zone).date().isoformat()
                if context.max_matches_per_day is not None and any(
                    daily_appearances[(person, path, day)] >= context.max_matches_per_day
                    for person, path in path_owners[ready]
                ):
                    index += 1
                    continue
                overlap = next(
                    (
                        (a, b)
                        for a, b in court_occupancy[court]
                        if proposed < b and a < proposed + occupation
                    ),
                    None,
                )
                if overlap is None:
                    options.append((proposed, court))
                    break
                index = bisect_left(available, overlap[1], lo=index + 1)
        if not options:
            return []
        start, court = chosen[ready] = min(options)
        insort(
            court_occupancy[court],
            (
                start,
                start
                + context.timing[ready]["estimated_play_minutes"]
                + context.timing[ready]["court_buffer_minutes"],
            ),
        )
        pending.remove(ready)
        appearances.update(context.candidates.get(ready, set()))
        day = timestamp(start).astimezone(context.zone).date().isoformat()
        daily_appearances.update((person, path, day) for person, path in path_owners[ready])
    return [
        Assignment(
            match_id=m,
            court_id=courts[court],
            start_at=timestamp(start),
            play_end_at=timestamp(start + context.timing[m]["estimated_play_minutes"]),
            court_release_at=timestamp(
                start
                + context.timing[m]["estimated_play_minutes"]
                + context.timing[m]["court_buffer_minutes"]
            ),
        )
        for m, (start, court) in chosen.items()
    ]


def solve_schedule(
    snapshot: Snapshot,
    *,
    time_limit_seconds: float | None = None,
    compact_groups: bool | None = None,
    local_match_ids: set[str] | None = None,
    not_before_by_match: dict[str, datetime] | None = None,
    blocked_court_intervals: dict[str, list[tuple[datetime, datetime]]] | None = None,
) -> dict[str, Any]:
    """Find a conservative feasible draft while preserving locks and local boundaries.

    No snapshot mutation occurs for input errors, proven infeasibility, timeout
    without an incumbent, or failure of the independent validator.
    """
    runtime = snapshot.config.get("runtime") or {}
    if compact_groups is None:
        compact_groups = runtime.get("schedule_objective") in {
            "compact_groups",
            "round_robin_first",
            "round_robin_batches",
        }
    division_batches = compact_groups and runtime.get("schedule_objective") == "round_robin_batches"
    round_robin_first = compact_groups and runtime.get("schedule_objective") in {
        "round_robin_first",
        "round_robin_batches",
    }
    baseline = [a.model_copy(deep=True) for a in snapshot.assignments]
    context, issues = build_context(snapshot)
    report: dict[str, Any] = {
        "status": "MODEL_INVALID",
        "issues": [],
        "assignments": [],
        "diagnostics": {"candidate_conflict_strategy": "conservative"},
    }

    def finish(status: str) -> dict[str, Any]:
        report["status"] = status
        report["issues"] = [i.model_dump(mode="json") for i in issues]
        if (
            context is not None
            and local_match_ids is not None
            and status in {"INFEASIBLE", "UNKNOWN"}
        ):
            report["diagnostics"]["expansion_proposal"] = expansion_proposal(
                snapshot, context, local_match_ids
            )
        return report

    if context is None:
        return finish("MODEL_INVALID")
    report["diagnostics"] = model_diagnostics(context)
    if local_match_ids is not None and not local_match_ids <= set(context.matches):
        issues.append(
            issue("LOCAL_SCOPE_INVALID", "Local scope contains unknown or inactive matches")
        )
        return finish("MODEL_INVALID")
    original = {a.match_id: a for a in baseline}
    if len(original) != len(baseline):
        issues.append(issue("ASSIGNMENT_DUPLICATE", "Existing schedule has duplicate assignments"))
        return finish("MODEL_INVALID")
    for match in context.matches.values():
        if match.status in {"in_progress", "completed"} and match.id not in original:
            issues.append(
                issue(
                    "IMMUTABLE_ASSIGNMENT_MISSING",
                    "Started matches require an existing assignment",
                    match.id,
                )
            )
    if issues and any(i.severity == "error" and i.status == "open" for i in issues):
        return finish("MODEL_INVALID")
    if time_limit_seconds is None:
        time_limit_seconds = (snapshot.config.get("runtime") or {}).get(
            "solver_time_limit_seconds", 60
        )
    if not isinstance(time_limit_seconds, (int, float)) or not math.isfinite(time_limit_seconds):
        issues.append(issue("SOLVER_LIMIT_INVALID", "Solver time limit must be a finite number"))
        return finish("MODEL_INVALID")
    report["diagnostics"]["solver_parameters"] = {
        "max_time_in_seconds": max(0.0, time_limit_seconds),
        "num_search_workers": 8 if compact_groups else 1,
        "random_seed": 0,
    }
    if time_limit_seconds <= 0:
        report["diagnostics"]["reason"] = (
            "No search budget; no feasibility or infeasibility conclusion"
        )
        return finish("UNKNOWN")
    choices = {m: context.choices(m) for m in context.matches}
    if division_batches:
        batch_grid = math.lcm(context.grid, runtime.get("batch_slot_minutes", 15))
        choices = {
            mid: [
                (start, court)
                for start, court in values
                if context.matches[mid].status in {"in_progress", "completed"}
                or (start - context.anchors[timestamp(start).astimezone(context.zone).date()])
                % batch_grid
                == 0
            ]
            for mid, values in choices.items()
        }
    # Operational facts constrain future choices, never the preserved planned
    # assignment of a match already started/completed. Actual occupancy is kept
    # separately from the immutable published assignment by the operator layer.
    try:
        limits = not_before_by_match or {}
        blocks = blocked_court_intervals or {}
        if set(limits) - context.matches.keys() or set(blocks) - context.windows.keys():
            raise ValueError("Operational limits reference unknown matches or courts")
        for value in [
            *limits.values(),
            *(v for rows in blocks.values() for pair in rows for v in pair),
        ]:
            if (
                not isinstance(value, datetime)
                or value.utcoffset() is None
                or value.second
                or value.microsecond
            ):
                raise ValueError("Operational limits require aware whole-minute timestamps")
        if any(start >= end for rows in blocks.values() for start, end in rows):
            raise ValueError("Operational court blocks require increasing intervals")
        court_ids = list(context.windows)
        for match_id, live_choices in choices.items():
            if context.matches[match_id].status in {"in_progress", "completed"}:
                continue
            earliest = minute(limits[match_id]) if match_id in limits else None
            occupation = (
                context.timing[match_id]["estimated_play_minutes"]
                + context.timing[match_id]["court_buffer_minutes"]
            )
            choices[match_id] = [
                (start, court)
                for start, court in live_choices
                if (earliest is None or start >= earliest)
                and not any(
                    start < minute(end) and minute(begin) < start + occupation
                    for begin, end in blocks.get(court_ids[court], [])
                )
            ]
    except (ValueError, TypeError) as exc:
        issues.append(issue("LIVE_SCHEDULE_LIMIT_INVALID", str(exc)))
        return finish("MODEL_INVALID")
    issues.extend(preflight(context, choices))
    if any(i.severity == "error" for i in issues):
        return finish("INFEASIBLE")
    if not context.matches:
        candidate = snapshot.model_copy(deep=True)
        candidate.assignments = []
        validation = validate_schedule(candidate, baseline_assignments=baseline)
        if any(i.severity == "error" and i.status == "open" for i in validation):
            issues.extend(validation)
            return finish("MODEL_INVALID")
        snapshot.assignments = []
        return finish("OPTIMAL")
    courts = list(context.windows)
    court_numbers = {court: index for index, court in enumerate(courts)}
    hint: dict[str, tuple[int, int]] = {}
    report["diagnostics"]["warm_start"] = {"status": "skipped_existing_or_local_schedule"}
    if (
        compact_groups
        and baseline
        and not validate_schedule(snapshot)
        and all(
            (minute(a.start_at), court_numbers.get(a.court_id, -1)) in choices[a.match_id]
            for a in baseline
            if a.match_id in context.matches
        )
    ):
        hint = {a.match_id: (minute(a.start_at), court_numbers[a.court_id]) for a in baseline}
        report["diagnostics"]["warm_start"] = {
            "status": "validated_existing_schedule",
            "assignment_count": len(hint),
        }
    if not baseline and local_match_ids is None:
        proposed = _greedy_hint(snapshot, context, choices)
        hint_status = "unavailable"
        if proposed:
            hint_candidate = snapshot.model_copy(deep=True)
            hint_candidate.assignments = proposed
            hint_errors = validate_schedule(hint_candidate, baseline_assignments=baseline)
            hint_status = "rejected_by_independent_validator"
            if not any(i.severity == "error" and i.status == "open" for i in hint_errors):
                hint = {
                    a.match_id: (minute(a.start_at), court_numbers[a.court_id]) for a in proposed
                }
                hint_status = "validated_hint_only"
        report["diagnostics"]["warm_start"] = {
            "status": hint_status,
            "assignment_count": len(hint),
            "committed_without_solver": False,
        }
    lower = min(start for values in choices.values() for start, _ in values)
    upper = max(
        start
        + context.timing[m]["estimated_play_minutes"]
        + context.timing[m]["court_buffer_minutes"]
        for m, values in choices.items()
        for start, _ in values
    )
    model = cp_model.CpModel()
    starts, court_vars, ends, releases = {}, {}, {}, {}
    intervals: dict[str, list] = {court: [] for court in courts}
    change_vars = []
    for match_id, match in context.matches.items():
        timing = context.timing[match_id]
        duration = timing["estimated_play_minutes"]
        occupation = duration + timing["court_buffer_minutes"]
        legal_starts = sorted({start for start, _ in choices[match_id]})
        legal_courts = sorted({court for _, court in choices[match_id]})
        independent_domains = compact_groups and len(choices[match_id]) == len(legal_starts) * len(
            legal_courts
        )
        if independent_domains:
            starts[match_id] = model.new_int_var_from_domain(
                cp_model.Domain.from_values(legal_starts), f"start_{match_id}"
            )
            court_vars[match_id] = model.new_int_var_from_domain(
                cp_model.Domain.from_values(legal_courts), f"court_{match_id}"
            )
        else:
            starts[match_id] = model.new_int_var(lower, upper, f"start_{match_id}")
            court_vars[match_id] = model.new_int_var(0, len(courts) - 1, f"court_{match_id}")
        if hint:
            model.add_hint(starts[match_id], hint[match_id][0])
            model.add_hint(court_vars[match_id], hint[match_id][1])
        if not independent_domains:
            model.add_allowed_assignments(
                [starts[match_id], court_vars[match_id]], choices[match_id]
            )
        ends[match_id] = starts[match_id] + duration
        releases[match_id] = starts[match_id] + occupation
        for court_id, court_index in court_numbers.items():
            present = model.new_bool_var(f"on_{match_id}_{court_index}")
            if hint:
                model.add_hint(present, int(hint[match_id][1] == court_index))
            model.add(court_vars[match_id] == court_index).only_enforce_if(present)
            model.add(court_vars[match_id] != court_index).only_enforce_if(present.Not())
            intervals[court_id].append(
                model.new_optional_fixed_size_interval_var(
                    starts[match_id], occupation, present, f"interval_{match_id}_{court_index}"
                )
            )
        prior = original.get(match_id)
        if prior is not None:
            immutable = match.status in {"in_progress", "completed"}
            outside_local = local_match_ids is not None and match_id not in local_match_ids
            if immutable or outside_local or is_time_locked(prior):
                model.add(starts[match_id] == minute(prior.start_at))
                # Preserving a locked assignment includes its original end times.
                if (
                    minute(prior.play_end_at) - minute(prior.start_at) != duration
                    or minute(prior.court_release_at) - minute(prior.start_at) != occupation
                ):
                    model.add_bool_or([])
            if immutable or outside_local or is_court_locked(prior):
                if prior.court_id in court_numbers:
                    model.add(court_vars[match_id] == court_numbers[prior.court_id])
                else:
                    model.add_bool_or([])
            same_start = model.new_bool_var(f"same_start_{match_id}")
            same_court = model.new_bool_var(f"same_court_{match_id}")
            model.add(starts[match_id] == minute(prior.start_at)).only_enforce_if(same_start)
            model.add(starts[match_id] != minute(prior.start_at)).only_enforce_if(same_start.Not())
            old_court = court_numbers.get(prior.court_id, -1)
            model.add(court_vars[match_id] == old_court).only_enforce_if(same_court)
            model.add(court_vars[match_id] != old_court).only_enforce_if(same_court.Not())
            changed = model.new_bool_var(f"changed_{match_id}")
            model.add_bool_and([same_start, same_court]).only_enforce_if(changed.Not())
            model.add_bool_or([same_start.Not(), same_court.Not()]).only_enforce_if(changed)
            change_vars.append(changed)
    for values in intervals.values():
        model.add_no_overlap(values)
    keep_groups_same_day = runtime.get("keep_round_robin_groups_same_day") is True
    if context.max_matches_per_day is not None or round_robin_first or keep_groups_same_day:
        days = sorted(context.anchors)
        on_day = {}
        for mid in context.matches:
            day_var = model.new_int_var(0, len(days) - 1, f"day_{mid}")
            if not compact_groups:
                model.add_allowed_assignments(
                    [starts[mid], day_var],
                    sorted(
                        {
                            (start, days.index(timestamp(start).astimezone(context.zone).date()))
                            for start, _ in choices[mid]
                        }
                    ),
                )
            for index, day in enumerate(days):
                present = model.new_bool_var(f"day_{index}_{mid}")
                model.add(day_var == index).only_enforce_if(present)
                model.add(day_var != index).only_enforce_if(present.Not())
                if compact_groups:
                    legal_day_starts = [
                        start
                        for start, _ in choices[mid]
                        if timestamp(start).astimezone(context.zone).date() == day
                    ]
                    if legal_day_starts:
                        model.add(starts[mid] >= min(legal_day_starts)).only_enforce_if(present)
                        model.add(starts[mid] <= max(legal_day_starts)).only_enforce_if(present)
                    else:
                        model.add(present == 0)
                on_day[(mid, day)] = present
        if context.max_matches_per_day is not None:
            for paths in context.player_paths().values():
                for path in paths:
                    for day in days:
                        model.add(
                            sum(on_day[(m, day)] for m in sorted(path))
                            <= context.max_matches_per_day
                        )
    if keep_groups_same_day:
        for group in round_robin_match_groups(snapshot, context.matches):
            for mid in group[1:]:
                for day in days:
                    model.add(on_day[mid, day] == on_day[group[0], day])
    for first, second in combinations(context.matches, 2):
        if not context.shared_people(first, second):
            continue
        rest = rest_minutes(context, first, second)
        transfer = transfer_minutes(context, first, second)
        delay: Any = rest
        if transfer:
            different_court = model.new_bool_var(f"transfer_{first}_{second}")
            if hint:
                model.add_hint(different_court, int(hint[first][1] != hint[second][1]))
            model.add(court_vars[first] != court_vars[second]).only_enforce_if(different_court)
            model.add(court_vars[first] == court_vars[second]).only_enforce_if(
                different_court.Not()
            )
            delay = rest + transfer * different_court
        first_before = model.new_bool_var(f"before_{first}_{second}")
        if hint:
            model.add_hint(first_before, int(hint[first][0] < hint[second][0]))
        model.add(starts[second] >= ends[first] + delay).only_enforce_if(first_before)
        model.add(starts[first] >= ends[second] + delay).only_enforce_if(first_before.Not())
    rank_deps = ranking_dependencies(snapshot, context)
    for match_id, dependencies in context.dependencies.items():
        if match_id not in context.matches:
            continue
        for prior_id in dependencies:
            if prior_id not in context.matches:
                issues.append(
                    issue(
                        "MATCH_REFERENCE_INVALID",
                        "A dependency is not an active actual match",
                        match_id,
                    )
                )
                return finish("MODEL_INVALID")
            lag = (
                max(
                    context.timing[match_id]["ranking_confirmation_minutes"],
                    context.timing[prior_id]["ranking_confirmation_minutes"],
                )
                if prior_id in rank_deps[match_id]
                else 0
            )
            lag = max(lag, context.deferred_lags.get(match_id, {}).get(prior_id, 0))
            model.add(starts[match_id] >= ends[prior_id] + lag)
    makespan = model.new_int_var(lower, upper, "latest_court_release")
    model.add_max_equality(makespan, list(releases.values()))
    if hint:
        model.add_hint(
            makespan,
            max(
                start
                + context.timing[m]["estimated_play_minutes"]
                + context.timing[m]["court_buffer_minutes"]
                for m, (start, _) in hint.items()
            ),
        )
    weight = upper - lower + 1
    if compact_groups:
        group_spans = []
        stage_spans = []
        for stage in snapshot.stages:
            mids = [m for m in context.matches if context.matches[m].stage_id == stage.id]
            if not mids:
                continue
            group_first = model.new_int_var(lower, upper, f"stage_first_{stage.id}")
            group_last = model.new_int_var(lower, upper, f"stage_last_{stage.id}")
            model.add_min_equality(group_first, [starts[m] for m in mids])
            model.add_max_equality(group_last, [ends[m] for m in mids])
            stage_spans.append(group_last - group_first)
            pools = [p for p in snapshot.pools if p.stage_id == stage.id]
            groups = [
                [
                    m
                    for m in mids
                    if all(
                        getattr(side, "entry_id", None) in pool.entry_ids
                        for side in (context.matches[m].side_a, context.matches[m].side_b)
                    )
                ]
                for pool in pools
            ] or ([mids] if stage.type == "round_robin" else [])
            for index, group in enumerate(groups):
                if not group:
                    continue
                group_first = model.new_int_var(lower, upper, f"pool_first_{stage.id}_{index}")
                group_last = model.new_int_var(lower, upper, f"pool_last_{stage.id}_{index}")
                model.add_min_equality(group_first, [starts[m] for m in group])
                model.add_max_equality(group_last, [ends[m] for m in group])
                group_spans.append(group_last - group_first)
        player_day_spans = []
        if context.max_matches_per_day is not None:
            day_starts, day_ends = {}, {}
            for mid in context.matches:
                for day in days:
                    masked_start = model.new_int_var(lower, upper, f"masked_start_{mid}_{day}")
                    masked_end = model.new_int_var(lower, upper, f"masked_end_{mid}_{day}")
                    present = on_day[(mid, day)]
                    model.add(masked_start == starts[mid]).only_enforce_if(present)
                    model.add(masked_start == upper).only_enforce_if(present.Not())
                    model.add(masked_end == ends[mid]).only_enforce_if(present)
                    model.add(masked_end == lower).only_enforce_if(present.Not())
                    day_starts[mid, day] = masked_start
                    day_ends[mid, day] = masked_end
            people = set().union(*context.candidates.values())
            for person in sorted(people):
                mids = [mid for mid in context.matches if person in context.candidates[mid]]
                if len(mids) < 2:
                    continue
                for day in days:
                    day_first = model.new_int_var(lower, upper, f"person_first_{person}_{day}")
                    day_last = model.new_int_var(lower, upper, f"person_last_{person}_{day}")
                    span = model.new_int_var(0, upper - lower, f"person_span_{person}_{day}")
                    model.add_min_equality(day_first, [day_starts[mid, day] for mid in mids])
                    model.add_max_equality(day_last, [day_ends[mid, day] for mid in mids])
                    model.add_max_equality(span, [0, day_last - day_first])
                    player_day_spans.append(span)
        compact_cost = (
            100 * sum(group_spans)
            + 10 * sum(player_day_spans)
            + sum(stage_spans)
            + makespan
            - lower
        )
        compact_bound = (upper - lower + 1) * (
            100 * len(group_spans) + 10 * len(player_day_spans) + len(stage_spans) + 1
        )
        if division_batches:
            slot_groups: dict[int, list] = {}
            division_ids = sorted({m.division_id for m in context.matches.values()})
            for division_id in division_ids:
                mids = [
                    mid
                    for mid in context.matches
                    if context.matches[mid].division_id == division_id
                ]
                slots = sorted({start for mid in mids for start, _ in choices[mid]})
                for slot in slots:
                    present = model.new_bool_var(f"batch_{division_id}_{slot}")
                    slot_groups.setdefault(slot, []).append(present)
                    for mid in mids:
                        model.add(starts[mid] != slot).only_enforce_if(present.Not())
                    if hint:
                        model.add_hint(present, int(any(hint[mid][0] == slot for mid in mids)))
            mixed_cost = []
            for slot, batch_members in slot_groups.items():
                occupied = model.new_bool_var(f"batch_occupied_{slot}")
                model.add_max_equality(occupied, batch_members)
                slot_weight = (
                    100
                    if timestamp(slot).astimezone(context.zone).date() == min(context.anchors)
                    else 1
                )
                mixed_cost.append(slot_weight * (sum(batch_members) - occupied))
            # Preserve the day-one priorities, then avoid mixed division slots.
            batch_weight = compact_bound + 1
            compact_cost += batch_weight * sum(mixed_cost)
            compact_bound += batch_weight * sum(
                len(members)
                * (
                    100
                    if timestamp(slot).astimezone(context.zone).date() == min(context.anchors)
                    else 1
                )
                for slot, members in slot_groups.items()
            )
        if round_robin_first:
            rr_stages = {stage.id for stage in snapshot.stages if stage.type == "round_robin"}
            rr_mids = [mid for mid in context.matches if context.matches[mid].stage_id in rr_stages]
            deferred_divisions = set(runtime.get("day_two_priority_division_ids", []))
            preferred_later = [
                mid for mid in rr_mids if context.matches[mid].division_id in deferred_divisions
            ]
            secondary_weight = compact_bound + 1
            primary_weight = secondary_weight * (len(preferred_later) + 1)
            model.minimize(
                primary_weight * sum(on_day[mid, day] for mid in rr_mids for day in days[1:])
                + secondary_weight * sum(on_day[mid, days[0]] for mid in preferred_later)
                + compact_cost
            )
        else:
            model.minimize(compact_cost)
        report["diagnostics"]["objective"] = {
            "strategy": "round_robin_batches"
            if division_batches
            else "round_robin_first"
            if round_robin_first
            else "compact_round_robin_groups",
            "priority": (
                ["day_one_round_robin_count", "defer_selected_divisions"]
                if round_robin_first
                else []
            )
            + (["minimize_mixed_division_slots"] if division_batches else [])
            + [
                "round_robin_group_span",
                "player_daily_span",
                "stage_span",
                "event_finish",
            ],
            "method": "day-one round-robin priority, deferred division priority, then weighted compactness"
            if round_robin_first
            else "weighted group span 100; player daily span 10; stage span 1; event finish 1",
        }
    else:
        model.minimize(sum(change_vars) * weight + makespan - lower)
    if not compact_groups:
        report["diagnostics"]["objective"]["change_weight"] = weight
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = 8 if compact_groups else 1
    solver.parameters.random_seed = 0
    # Large legal-slot tables can consume the full budget in presolve. A complete
    # independently validated hint lets CP-SAT start search without that expansion.
    solver.parameters.cp_model_presolve = bool(division_batches) or not (
        bool(hint) and len(context.matches) >= 200
    )
    solver.parameters.stop_after_first_solution = (
        not compact_groups and bool(hint) and len(context.matches) >= 200
    )
    report["diagnostics"]["solver_parameters"]["cp_model_presolve"] = (
        solver.parameters.cp_model_presolve
    )
    report["diagnostics"]["solver_parameters"]["stop_after_first_solution"] = (
        solver.parameters.stop_after_first_solution
    )
    status = solver.solve(model)
    name = solver.status_name(status)
    report["diagnostics"]["solver_status"] = name
    report["diagnostics"]["wall_time_seconds"] = solver.wall_time
    if status not in {cp_model.FEASIBLE, cp_model.OPTIMAL}:
        if status == cp_model.INFEASIBLE:
            issues.append(
                issue(
                    "SCHEDULE_MODEL_INFEASIBLE",
                    "No schedule satisfies the conservative model",
                    suggested_action="Review listed constraints and propose organizer changes",
                )
            )
        return finish(name)
    assignments = []
    for match_id in context.matches:
        start = solver.value(starts[match_id])
        timing = context.timing[match_id]
        assignments.append(
            Assignment(
                match_id=match_id,
                court_id=courts[solver.value(court_vars[match_id])],
                start_at=timestamp(start),
                play_end_at=timestamp(start + timing["estimated_play_minutes"]),
                court_release_at=timestamp(
                    start + timing["estimated_play_minutes"] + timing["court_buffer_minutes"]
                ),
                locks=original[match_id].locks.copy() if match_id in original else {},
            )
        )
    candidate = snapshot.model_copy(deep=True)
    candidate.assignments = assignments
    validation = validate_schedule(candidate, baseline_assignments=baseline)
    report["diagnostics"]["independent_validation"] = "passed"
    if any(i.severity == "error" and i.status == "open" for i in validation):
        issues.extend(validation)
        report["diagnostics"]["independent_validation"] = "failed"
        return finish("MODEL_INVALID")
    issues.extend(validation)
    report["diagnostics"]["changes"] = schedule_diff(baseline, assignments)
    report["assignments"] = [a.model_dump(mode="json") for a in assignments]
    snapshot.assignments = assignments
    return finish(name)
