"""Rehearse junior-high encouragement capacity on a copy of a pre-event schedule."""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from squash_scheduler.domain.encouragement import ELIGIBLE_DIVISIONS
from squash_scheduler.domain.encouragement_draws import (
    KIND,
    apply_draw,
    is_encouragement,
    propose_draw,
)
from squash_scheduler.domain.formats import active_entries, pending_format_divisions
from squash_scheduler.domain.models import Assignment, Issue, Match, Snapshot, stable_id
from squash_scheduler.domain.rankings import effective_rules, import_results, resolve_side
from squash_scheduler.scheduling import solve_schedule, validate_schedule
from squash_scheduler.storage.repository import Repository

ACTOR = "encouragement-capacity-rehearsal"
LIMITATION = "One simulated result and draw scenario. Unresolved players use the workspace policy."


class RehearsalError(ValueError):
    def __init__(self, *issue_codes: str):
        self.issue_codes = list(issue_codes)
        super().__init__(", ".join(issue_codes))


@dataclass(frozen=True)
class ScenarioDivision:
    division_id: str
    division_name: str
    first_round_count: int
    eligible_count: int
    match_ids: tuple[str, ...]
    latest_first_round_play_end: datetime
    earliest_start: datetime


def _open_error_codes(issues: list[Issue]) -> list[str]:
    return sorted({i.code for i in issues if i.severity == "error" and i.status == "open"})


def _simulated_result(
    snapshot: Snapshot, match: Match, seeded: set[str], rng: random.Random
) -> dict[str, Any]:
    first = resolve_side(snapshot, match.side_a)
    second = resolve_side(snapshot, match.side_b)
    if not first or not second or first == second:
        raise RehearsalError("FIRST_ROUND_SIDES_UNRESOLVED")
    eligible_losers = [entry for entry in (first, second) if entry not in seeded]
    loser = rng.choice(eligible_losers or [first, second])
    rules = (match.rule_snapshot or {}).get("match_rules") or effective_rules(
        snapshot, match.stage_id
    )
    best_of = rules.get("best_of_games")
    target = rules.get("points_to_win_game")
    margin = rules.get("win_by_points")
    if (
        type(best_of) is not int
        or best_of < 1
        or best_of % 2 != 1
        or type(target) is not int
        or target < 1
        or type(margin) is not int
        or margin < 1
    ):
        raise RehearsalError("FIRST_ROUND_SCORING_INVALID")
    score = max(target, margin)
    if rules.get("point_cap_mode") == "enabled":
        cap = rules.get("point_cap")
        if type(cap) is not int or cap < target:
            raise RehearsalError("FIRST_ROUND_SCORING_INVALID")
        score = min(score, cap)
    winner = second if loser == first else first
    game = {"side_a": score if winner == first else 0, "side_b": score if winner == second else 0}
    return {
        "match_id": match.id,
        "outcome": "played",
        "status": "confirmed",
        "winner_id": winner,
        "games": [game.copy() for _ in range(best_of // 2 + 1)],
    }


def prepare_scenario(
    snapshot: Snapshot, *, seed: int, confirmation_delay_minutes: int
) -> tuple[Snapshot, list[ScenarioDivision]]:
    """Use simulated results and reviewed draws on a copy of a complete pre-event schedule."""
    if confirmation_delay_minutes < 0:
        raise RehearsalError("CONFIRMATION_DELAY_INVALID")
    if (
        snapshot.results
        or any(m.status in {"in_progress", "completed"} for m in snapshot.matches)
        or any(c.get("kind") == "live_match_event" for c in snapshot.changes)
    ):
        raise RehearsalError("PRE_EVENT_WORKSPACE_REQUIRED")
    if any(is_encouragement(s) for s in snapshot.stages) or any(
        d.get("kind") == KIND and d.get("status") != "superseded" for d in snapshot.draws
    ):
        raise RehearsalError("EXISTING_ENCOURAGEMENT_DRAW")
    if pending_format_divisions(snapshot):
        raise RehearsalError("MAIN_DRAW_PENDING")
    if codes := _open_error_codes(validate_schedule(snapshot)):
        raise RehearsalError(*codes)

    working = snapshot.model_copy(deep=True)
    original_assignments = {a.match_id: a for a in snapshot.assignments}
    rng = random.Random(seed)
    groups = []
    for division in sorted(snapshot.divisions, key=lambda d: (d.name, d.id)):
        plan = division.format_plan or {}
        if (
            division.name not in ELIGIBLE_DIVISIONS
            or division.category != "school"
            or division.status in {"cancelled", "merged", "exhibition_only"}
            or plan.get("template_id") != "single_elimination"
            or plan.get("encouragement_enabled") is False
            or len(active_entries(snapshot, division.id)) < 2
        ):
            continue
        stage_id = stable_id("stage", division.id, "elimination")
        first_round = sorted(
            (
                m
                for m in working.matches
                if m.stage_id == stage_id and m.kind == "competitive" and m.round == 1
            ),
            key=lambda m: m.id,
        )
        if not first_round or any(m.id not in original_assignments for m in first_round):
            raise RehearsalError("FIRST_ROUND_ASSIGNMENTS_MISSING")
        rules = {**working.config.get("draw_defaults", {}), **division.draw_overrides}
        seeded = {row["entry_id"] for row in rules.get("seeded_entries", [])}
        import_results(
            working,
            {
                "actor_id": ACTOR,
                "results": [
                    _simulated_result(working, match, seeded, rng) for match in first_round
                ],
            },
        )
        proposal = propose_draw(working, division.id, seed, ACTOR, "Simulate an eligible draw")
        working, audit = apply_draw(
            working, proposal, proposal["proposal_hash"], ACTOR, "Confirm an in-memory draw"
        )
        latest_end = max(original_assignments[m.id].play_end_at for m in first_round)
        groups.append(
            ScenarioDivision(
                division_id=division.id,
                division_name=division.name,
                first_round_count=len(first_round),
                eligible_count=len(audit["entry_ids"]),
                match_ids=tuple(audit["match_ids"]),
                latest_first_round_play_end=latest_end,
                earliest_start=latest_end + timedelta(minutes=confirmation_delay_minutes),
            )
        )
    return working, groups


def _group_report(
    group: ScenarioDivision,
    assignments: dict[str, Assignment],
    zone: ZoneInfo,
    court_labels: dict[str, str],
) -> dict[str, Any]:
    def local(value: datetime) -> str:
        return value.astimezone(zone).isoformat(timespec="minutes")

    windows = sorted(
        (assignments[mid] for mid in group.match_ids if mid in assignments),
        key=lambda a: (a.start_at, a.court_id),
    )
    return {
        "division_id": group.division_id,
        "division": group.division_name,
        "first_round_matches": group.first_round_count,
        "eligible_players": group.eligible_count,
        "encouragement_matches": len(group.match_ids),
        "latest_first_round_play_end": local(group.latest_first_round_play_end),
        "earliest_encouragement_start": local(group.earliest_start),
        "court_windows": [
            {
                "court_id": a.court_id,
                "court": court_labels.get(a.court_id, a.court_id),
                "start": local(a.start_at),
                "play_end": local(a.play_end_at),
                "court_release": local(a.court_release_at),
            }
            for a in windows
        ],
    }


def _validate_solution(
    snapshot: Snapshot,
    baseline: dict[str, Assignment],
    not_before: dict[str, datetime],
    *,
    preserve_main: bool,
) -> dict[str, Assignment]:
    assignments = {a.match_id: a for a in snapshot.assignments}
    if assignments.keys() != baseline.keys() | not_before.keys():
        raise RehearsalError("REHEARSAL_ASSIGNMENTS_INCOMPLETE")
    if preserve_main and any(assignments[mid] != a for mid, a in baseline.items()):
        raise RehearsalError("ORIGINAL_ASSIGNMENT_CHANGED")
    if any(assignments[mid].start_at < start for mid, start in not_before.items()):
        raise RehearsalError("CONFIRMATION_DELAY_VIOLATION")
    if codes := _open_error_codes(
        validate_schedule(snapshot, baseline_assignments=list(baseline.values()))
    ):
        raise RehearsalError(*codes)
    return assignments


def check(
    snapshot: Snapshot,
    *,
    seed: int,
    confirmation_delay_minutes: int,
    time_limit_seconds: float,
    replan_main: bool = False,
) -> dict[str, Any]:
    if not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        raise RehearsalError("TIME_LIMIT_INVALID")
    working, groups = prepare_scenario(
        snapshot, seed=seed, confirmation_delay_minutes=confirmation_delay_minutes
    )
    zone = ZoneInfo(snapshot.config["event"]["timezone"])
    court_labels = {court["id"]: court["label"] for court in snapshot.config["courts"]}
    not_before = {mid: group.earliest_start for group in groups for mid in group.match_ids}
    report = {
        "source_revision": snapshot.id,
        "mode": "replan_main" if replan_main else "fixed_main",
        "seed": seed,
        "confirmation_delay_minutes": confirmation_delay_minutes,
        "time_limit_seconds": time_limit_seconds,
        "limitation": LIMITATION,
        "existing_matches": len(snapshot.assignments),
        "encouragement_matches": len(not_before),
        "total_matches": len(snapshot.assignments) + len(not_before),
        "divisions": [_group_report(group, {}, zone, court_labels) for group in groups],
    }
    if not not_before:
        return {**report, "status": "no_matches", "original_assignments_preserved": True}
    solution = solve_schedule(
        working,
        time_limit_seconds=time_limit_seconds,
        local_match_ids=None if replan_main else set(not_before),
        not_before_by_match=not_before,
    )
    solver_status = solution["status"]
    report["solver_status"] = solver_status
    if solver_status not in {"FEASIBLE", "OPTIMAL"}:
        status = {"INFEASIBLE": "infeasible", "UNKNOWN": "timeout"}.get(solver_status, "invalid")
        return {
            **report,
            "status": status,
            "issue_codes": sorted({issue["code"] for issue in solution["issues"]}),
        }

    baseline = {a.match_id: a for a in snapshot.assignments}
    assignments = _validate_solution(working, baseline, not_before, preserve_main=not replan_main)
    moved = sum(assignments[mid] != a for mid, a in baseline.items())
    if replan_main:
        frozen = snapshot.model_copy(deep=True)
        frozen.assignments = [assignments[mid] for mid in baseline]
        replay, groups = prepare_scenario(
            frozen, seed=seed, confirmation_delay_minutes=confirmation_delay_minutes
        )
        replay.assignments = working.assignments
        replay_floor = {mid: group.earliest_start for group in groups for mid in group.match_ids}
        _validate_solution(
            replay, {a.match_id: a for a in frozen.assignments}, replay_floor, preserve_main=True
        )
        report["frozen_main_replay_verified"] = True
    return {
        **report,
        "status": "feasible",
        "original_assignments_preserved": moved == 0,
        "main_assignments_moved": moved,
        "divisions": [_group_report(group, assignments, zone, court_labels) for group in groups],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--confirmation-delay-minutes", type=int, default=15)
    parser.add_argument("--time-limit-seconds", type=float, default=60)
    parser.add_argument(
        "--replan-main", action="store_true", help="Allow a new main schedule on the pre-event copy"
    )
    args = parser.parse_args()
    report: dict[str, Any]
    try:
        snapshot = Repository(args.workspace).load()
    except (OSError, ValueError, KeyError, TypeError):
        report = {"status": "invalid", "issue_codes": ["WORKSPACE_LOAD_FAILED"]}
    else:
        try:
            report = check(
                snapshot,
                seed=args.seed,
                confirmation_delay_minutes=args.confirmation_delay_minutes,
                time_limit_seconds=args.time_limit_seconds,
                replan_main=args.replan_main,
            )
        except RehearsalError as exc:
            report = {"status": "invalid", "issue_codes": exc.issue_codes}
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            code = str(exc).partition(":")[0]
            report = {
                "status": "invalid",
                "issue_codes": [
                    code if re.fullmatch(r"[A-Z][A-Z_]+", code) else "REHEARSAL_FAILED"
                ],
            }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"feasible": 0, "no_matches": 0, "infeasible": 1, "timeout": 1}.get(report["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
