"""Validated result batches, explicit ranking denominators, and reviewed advancement."""

from __future__ import annotations

from .match_rules import effective_match_rules

import math
from typing import Any

from .coach_tiebreak import order_tied_entries
from .draws import draw_inputs
from .matches import dependency_map
from .models import (
    DrawSlotSide,
    EntrySide,
    Issue,
    LoserSide,
    Match,
    MatchSide,
    PoolRankSide,
    Snapshot,
    WinnerSide,
    canonical_hash,
    new_id,
    utcnow,
)
from .validation import target_basis

OUTCOMES = {"played", "walkover", "retired", "double_forfeit", "cancelled", "pending"}


def _approval_current(snapshot: Snapshot, kind: str, value: dict[str, Any]) -> bool:
    """Require an actual approval action bound to this precise target content."""
    target_id, approval_id = value.get("id"), value.get("approval_id")
    if not target_id or not approval_id:
        return False
    basis = target_basis(value)
    return any(
        action.get("id") == approval_id
        and action.get("action") == "approve"
        and action.get("target_type") == kind
        and action.get("target_id") == target_id
        and action.get("basis_hash") == basis
        and action.get("status") != "stale"
        for action in snapshot.approvals
    )


def effective_rules(snapshot: Snapshot, stage_id: str) -> dict[str, Any]:
    stage = next(s for s in snapshot.stages if s.id == stage_id)
    division = next(d for d in snapshot.divisions if d.id == stage.division_id)
    stage_rules = snapshot.config.get("rule_sets", {}).get(stage.rule_set_id, {})
    rules = {
        **snapshot.config.get("match_rule_defaults", {}),
        **division.rule_overrides.get("match_rules", {}),
        **stage_rules.get("match_rules", {}),
    }
    scoring = {
        "win_points": 2,
        "loss_points": 1,
        "forfeit_points": 0,
        **snapshot.config.get("policy", {}).get("scoring_defaults", {}),
        **division.rule_overrides.get("scoring", {}),
        **stage_rules.get("scoring", {}),
    }
    rules["scoring"] = scoring
    return rules


def _current_results(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    return {r["match_id"]: r for r in snapshot.results if r.get("status") == "confirmed"}


def ranking_basis(snapshot: Snapshot, stage_id: str) -> str:
    stage = next(s for s in snapshot.stages if s.id == stage_id)
    matches = sorted((m for m in snapshot.matches if m.stage_id == stage_id), key=lambda m: m.id)
    ids = {m.id for m in matches}
    return canonical_hash(
        {
            "stage": stage.model_dump(mode="json"),
            "pools": sorted(
                (p.model_dump(mode="json") for p in snapshot.pools if p.stage_id == stage_id),
                key=lambda p: p["id"],
            ),
            "matches": [m.model_dump(mode="json") for m in matches],
            "results": sorted(
                (
                    r
                    for r in snapshot.results
                    if r.get("match_id") in ids and r.get("status") == "confirmed"
                ),
                key=lambda r: r["match_id"],
            ),
            "exclusions": sorted(
                (e for e in snapshot.exclusions if e.get("match_id") in ids),
                key=lambda e: e.get("id", ""),
            ),
            "rules": effective_rules(snapshot, stage_id),
            **(
                {"coach_competition_rules": True}
                if snapshot.config.get("runtime", {}).get("coach_competition_rules")
                else {}
            ),
        }
    )


def resolve_side(snapshot: Snapshot, side: MatchSide, _seen: set[str] | None = None) -> str | None:
    seen = _seen or set()
    if isinstance(side, EntrySide):
        return side.entry_id if any(e.id == side.entry_id for e in snapshot.entries) else None
    if isinstance(side, DrawSlotSide):
        draw = next((d for d in snapshot.draws if d.get("id") == side.draw_run_id), {})
        if draw.get("kind") == "deferred_slots":
            from .deferred_draws import _record_slot

            try:
                record, _ = _record_slot(snapshot, side)
            except (ValueError, KeyError, TypeError, RecursionError):
                return None
            if record.get("phase") != "execution":
                return None
            return next(
                s["entry_id"] for s in record["output"]["slots"] if s["slot_id"] == side.slot_id
            )
        if draw.get("status") != "approved" or not _approval_current(snapshot, "draw", draw):
            return None
        try:
            current_inputs = draw_inputs(snapshot, draw["division_id"], draw["random_seed"])
        except (KeyError, StopIteration, TypeError, ValueError):
            return None
        if draw.get("input_hash") != canonical_hash(current_inputs):
            return None
        return next(
            (
                s.get("entry_id")
                for s in (draw.get("output") or {}).get("slots", [])
                if str(s.get("slot_id")) == side.slot_id
            ),
            None,
        )
    if isinstance(side, (WinnerSide, LoserSide)):
        if side.match_id in seen:
            return None
        match = next((m for m in snapshot.matches if m.id == side.match_id), None)
        result = _current_results(snapshot).get(side.match_id)
        if (
            not match
            or not result
            or result.get("outcome") not in {"played", "walkover", "retired"}
        ):
            return None
        winner = result.get("winner_id")
        candidates = [
            resolve_side(snapshot, s, seen | {side.match_id}) for s in (match.side_a, match.side_b)
        ]
        if winner not in candidates or winner is None:
            return None
        if isinstance(side, WinnerSide):
            return winner
        return next((e for e in candidates if e is not None and e != winner), None)
    if isinstance(side, PoolRankSide):
        pool = next((p for p in snapshot.pools if p.id == side.pool_id), None)
        if not pool or pool.stage_id in seen or side.rank > pool.advancement_count:
            return None
        rankings = compute_rankings(snapshot, pool.stage_id)
        group = next((g for g in rankings["groups"] if g["pool_id"] == pool.id), None)
        if not group or group["status"] != "final":
            return None
        eligible = {e.id for e in snapshot.entries if e.status == "active"}
        qualified = [r["entry_id"] for r in group["standings"] if r["entry_id"] in eligible]
        return qualified[side.rank - 1] if side.rank <= len(qualified) else None
    return None


def _game_scores(game: dict[str, Any]) -> tuple[int, int]:
    if not isinstance(game, dict) or set(game) != {"side_a", "side_b"}:
        raise ValueError("Each game requires side_a and side_b integer scores")
    a, b = game["side_a"], game["side_b"]
    if type(a) is not int or type(b) is not int or min(a, b) < 0:
        raise ValueError("Game scores must be nonnegative integers")
    return a, b


def _terminal_game(a: int, b: int, rules: dict[str, Any]) -> bool:
    target, margin = rules.get("points_to_win_game"), rules.get("win_by_points")
    cap_mode, cap = rules.get("point_cap_mode"), rules.get("point_cap")
    if type(target) is not int or target < 1 or type(margin) is not int or margin < 1:
        raise ValueError("Points to win and winning margin must be configured")
    if cap_mode not in {"none", "enabled"}:
        raise ValueError("Point cap mode must be configured")
    if cap_mode == "enabled" and (type(cap) is not int or cap < target):
        raise ValueError("Enabled point cap must be at least the target score")
    high, low = max(a, b), min(a, b)
    if high == low:
        return False
    terminal = max(target, low + margin)
    below_cap = True
    if cap_mode == "enabled":
        assert type(cap) is int  # Validated above; retain the narrowing for score arithmetic.
        terminal = min(terminal, cap)
        below_cap = low < cap
    return high == terminal and below_cap


def _validate_result(
    snapshot: Snapshot, match: Match, result: dict[str, Any], rules: dict[str, Any]
) -> None:
    if result.get("outcome") not in OUTCOMES or result.get("status") != "confirmed":
        raise ValueError("Result requires a supported outcome and confirmed status")
    first, second = resolve_side(snapshot, match.side_a), resolve_side(snapshot, match.side_b)
    if not first or not second or first == second:
        raise ValueError("Both match participants must be resolved and distinct before results")
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    persons = {e.id: registrations.get(e.registration_id) for e in snapshot.entries}
    if persons.get(first) is not None and persons.get(first) == persons.get(second):
        raise ValueError("The same actual participant cannot play both sides")
    scoring = rules.get("scoring", {})
    if any(
        type(scoring.get(k)) not in {int, float} or not math.isfinite(scoring[k]) or scoring[k] < 0
        for k in ("win_points", "loss_points", "forfeit_points")
    ):
        raise ValueError("Ranking point rules must be finite nonnegative numbers")
    outcome = result["outcome"]
    games = result.get("games", [])
    if not isinstance(games, list):
        raise ValueError("games must be an array")  # noqa: TRY004 - uniform payload validation API
    winner = result.get("winner_id")
    if outcome in {"played", "walkover", "retired"} and winner not in {first, second}:
        raise ValueError("winner_id must be one of the resolved match entries")
    if outcome in {"double_forfeit", "cancelled", "pending"} and winner is not None:
        raise ValueError("This outcome cannot declare a winner")
    if outcome in {"walkover", "double_forfeit", "cancelled", "pending"} and games:
        raise ValueError("Unplayed outcomes cannot contain synthetic game scores")
    if outcome in {"played", "retired"}:
        best_of = rules.get("best_of_games")
        if type(best_of) is not int or best_of < 1 or best_of % 2 != 1:
            raise ValueError("An odd best_of_games rule is required")
        needed = best_of // 2 + 1
        wins = [0, 0]
        for index, game in enumerate(games):
            if max(wins) >= needed:
                raise ValueError("Games cannot continue after match victory")
            a, b = _game_scores(game)
            finished = _terminal_game(a, b, rules)
            if not finished and not (outcome == "retired" and index == len(games) - 1):
                raise ValueError("A game score does not satisfy the configured scoring rules")
            if not finished:
                terminal = max(rules["points_to_win_game"], min(a, b) + rules["win_by_points"])
                if rules["point_cap_mode"] == "enabled":
                    terminal = min(terminal, rules["point_cap"])
                if max(a, b) >= terminal:
                    raise ValueError("Retirement score exceeds the point at which the game ended")
            if finished:
                wins[0 if a > b else 1] += 1
        if outcome == "played" and (
            max(wins) != needed or winner != (first if wins[0] > wins[1] else second)
        ):
            raise ValueError(
                "Normal completion requires the declared winner to reach the winning game count"
            )
        if outcome == "retired" and max(wins) >= needed:
            raise ValueError("A completed match cannot be recorded as retired")
    if outcome in {"walkover", "retired", "double_forfeit"}:
        key = {
            "walkover": "walkover_rule",
            "retired": "retirement_rule",
            "double_forfeit": "double_forfeit_rule",
        }[outcome]
        special = rules.get(key)
        required = {"include_in_rankings", "counts_as_played"}
        required |= (
            {"side_a_points", "side_b_points"}
            if outcome == "double_forfeit"
            else {"winner_points", "loser_points"}
        )
        if not isinstance(special, dict) or not required <= special.keys():
            raise ValueError(
                f"{key} requires explicit ranking inclusion, appearance and point rules"
            )
        if any(type(special[k]) is not bool for k in ("include_in_rankings", "counts_as_played")):
            raise ValueError("Special outcome inclusion/appearance settings must be boolean")
        if outcome != "retired" and special["counts_as_played"]:
            raise ValueError("An unplayed forfeit cannot count as an actual appearance")
        for key in required - {"include_in_rankings", "counts_as_played"}:
            if (
                type(special[key]) not in {int, float}
                or not math.isfinite(special[key])
                or special[key] < 0
            ):
                raise ValueError("Special outcome points must be nonnegative numbers")
        denominator = rules.get("ranking_denominator_rule")
        if not isinstance(denominator, dict) or type(denominator.get(outcome)) is not bool:
            raise ValueError(
                "ranking_denominator_rule must explicitly include or exclude this outcome"
            )


def import_results(snapshot: Snapshot, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the entire batch on a copy, retaining superseded records on corrections."""
    batch = payload.get("results")
    if not isinstance(batch, list) or not batch:
        raise ValueError("results must be a nonempty array")
    if len({r.get("match_id") for r in batch}) != len(batch):
        raise ValueError("Result batch contains duplicate matches")
    working = snapshot.model_copy(deep=True)
    matches = {m.id: m for m in working.matches}
    graph = dependency_map(working)
    imported = []
    # Dependencies inside a batch are resolved in topological order, independent of input order.
    remaining = list(batch)
    while remaining:
        ready = [
            r
            for r in remaining
            if r.get("match_id") in matches
            and resolve_side(working, matches[r["match_id"]].side_a)
            and resolve_side(working, matches[r["match_id"]].side_b)
        ]
        if not ready:
            raise ValueError("Results reference unknown matches or unresolved advancement")
        for raw in ready:
            match = matches[raw["match_id"]]
            if match.status in {"excluded", "cancelled"}:
                raise ValueError("Excluded/cancelled matches cannot accept competitive results")
            previous = _current_results(working).get(match.id)
            if previous:
                downstream = {match.id}
                while True:
                    expanded = downstream | {
                        mid for mid, deps in graph.items() if deps & downstream
                    }
                    if expanded == downstream:
                        break
                    downstream = expanded
                if any(
                    matches[mid].status in {"in_progress", "completed"}
                    for mid in downstream - {match.id}
                ):
                    raise ValueError(
                        "Result correction affects started downstream play; manual competition review is required"
                    )
                if not str(payload.get("reason", "")).strip():
                    raise ValueError("Result correction requires a reason")
            rules = previous.get("rule_snapshot") if previous else None
            rules = (
                rules
                or ((match.rule_snapshot or {}).get("match_rules"))
                or effective_match_rules(working, match)
            )
            result = {
                k: raw[k]
                for k in ("match_id", "outcome", "games", "winner_id", "status")
                if k in raw
            }
            result.setdefault("games", [])
            _validate_result(working, match, result, rules)
            if previous:
                previous["status"] = "superseded"
            result.update(
                id=new_id(),
                rule_snapshot=rules,
                rule_hash=canonical_hash(rules),
                created_at=utcnow().isoformat(),
                actor_id=payload.get("actor_id"),
                reason=payload.get("reason"),
                supersedes_id=previous.get("id") if previous else None,
            )
            working.results.append(result)
            if result["outcome"] not in {"pending", "cancelled"}:
                match.status = "completed"
            imported.append(result["id"])
            remaining.remove(raw)
    snapshot.results = working.results
    snapshot.matches = working.matches
    return {"imported_result_ids": imported, "imported_count": len(imported), "status": "accepted"}


def compute_rankings(snapshot: Snapshot, stage_id: str) -> dict[str, Any]:
    stage = next(s for s in snapshot.stages if s.id == stage_id)
    if stage.type != "round_robin":
        raise ValueError("Rankings require a round robin stage")
    basis = ranking_basis(snapshot, stage_id)
    pools = [p for p in snapshot.pools if p.stage_id == stage_id]
    active = [
        e.id
        for e in snapshot.entries
        if e.effective_division_id == stage.division_id and e.status == "active"
    ]
    groups: list[tuple[str | None, list[str]]] = [(p.id, p.entry_ids) for p in pools] or [
        (None, active)
    ]
    current = _current_results(snapshot)
    all_issues: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    def issue(code: str, message: str) -> None:
        all_issues.append(
            Issue(
                code=code, gate="G6", entity_type="stage", entity_id=stage_id, message=message
            ).model_dump(mode="json")
        )

    for pool_id, entry_ids in groups:
        rows: dict[str, dict[str, Any]] = {
            entry: {
                "entry_id": entry,
                "pool_id": pool_id,
                "rank": None,
                "wins": 0,
                "losses": 0,
                "forfeits": 0,
                "played_count": 0,
                "adjudicated_count": 0,
                "ranked_wins": 0,
                "points": 0,
                "games_for": 0,
                "games_against": 0,
                "points_for": 0,
                "points_against": 0,
            }
            for entry in entry_ids
        }
        group_matches = [
            m
            for m in snapshot.matches
            if m.stage_id == stage_id
            and m.kind != "exhibition"
            and isinstance(m.side_a, EntrySide)
            and isinstance(m.side_b, EntrySide)
            and m.side_a.entry_id in rows
            and m.side_b.entry_id in rows
        ]
        exclusions = any(m.status in {"excluded", "cancelled"} for m in group_matches)
        missing = len(group_matches) != len(entry_ids) * (len(entry_ids) - 1) // 2
        incomplete, rule_problem, pending_results = exclusions or missing, False, False
        head_to_head: dict[frozenset[str], str] = {}
        quotient_records: list[dict[str, Any]] = []
        for match in group_matches:
            if match.status in {"excluded", "cancelled"}:
                continue
            result = current.get(match.id)
            if not result or result.get("outcome") in {"pending", "cancelled"}:
                incomplete = True
                pending_results = True
                continue
            rules = result.get("rule_snapshot") or effective_rules(snapshot, stage_id)
            try:
                _validate_result(snapshot, match, result, rules)
            except ValueError as exc:
                issue("RESULT_RULES_UNRESOLVED", str(exc))
                rule_problem = True
                continue
            # group_matches includes only direct-entry round-robin games.
            assert isinstance(match.side_a, EntrySide)
            assert isinstance(match.side_b, EntrySide)
            first, second = match.side_a.entry_id, match.side_b.entry_id
            outcome, winner = result["outcome"], result.get("winner_id")
            a, b = rows[first], rows[second]
            special: dict[str, Any] | None = None
            if outcome in {"walkover", "retired", "double_forfeit"}:
                special = rules[
                    {
                        "walkover": "walkover_rule",
                        "retired": "retirement_rule",
                        "double_forfeit": "double_forfeit_rule",
                    }[outcome]
                ]
            included = special["include_in_rankings"] if special else True
            actual = special["counts_as_played"] if special else True
            adjudicated = rules["ranking_denominator_rule"][outcome] if special else True
            a["played_count"] += int(actual)
            b["played_count"] += int(actual)
            if not included:
                incomplete = True
                continue
            quotient_records.append({"a": first, "b": second, "games": result.get("games", []), "target": rules.get("points_to_win_game", 11), "margin": rules.get("win_by_points", 2)})
            for entry, row in ((first, a), (second, b)):
                row["adjudicated_count"] += int(adjudicated)
                if winner == entry:
                    row["wins"] += 1
                    row["ranked_wins"] += int(adjudicated)
                elif winner:
                    row["losses"] += 1
                if outcome == "double_forfeit" or (
                    outcome in {"walkover", "retired"} and winner != entry
                ):
                    row["forfeits"] += 1
                if outcome == "double_forfeit":
                    assert special is not None  # Validated outcome-specific rule above.
                    row["points"] += special["side_a_points" if entry == first else "side_b_points"]
                elif special:
                    row["points"] += special["winner_points" if winner == entry else "loser_points"]
                else:
                    row["points"] += rules["scoring"][
                        "win_points" if winner == entry else "loss_points"
                    ]
            if winner:
                head_to_head[frozenset((first, second))] = winner
            for game in result.get("games", []):
                x, y = _game_scores(game)
                a["points_for"] += x
                a["points_against"] += y
                b["points_for"] += y
                b["points_against"] += x
                if _terminal_game(x, y, rules):
                    a["games_for"] += int(x > y)
                    a["games_against"] += int(y > x)
                    b["games_for"] += int(y > x)
                    b["games_against"] += int(x > y)
        for row in rows.values():
            denominator = row["adjudicated_count"]
            row["average_points"] = row["points"] / denominator if denominator else None
            row["win_rate"] = row["ranked_wins"] / denominator if denominator else None
            row["average_points_denominator"] = "adjudicated_count"
            row["win_rate_denominator"] = "adjudicated_count"
            row["game_difference"] = row["games_for"] - row["games_against"]
            row["point_difference"] = row["points_for"] - row["points_against"]
        status = "provisional" if incomplete else "final"
        tie_groups: list[list[str]] = []
        ordered: list[dict[str, Any]] = []
        for points in sorted({r["points"] for r in rows.values()}, reverse=True):
            tied = [r for r in rows.values() if r["points"] == points]
            if snapshot.config.get("runtime", {}).get("coach_competition_rules") and len(tied) > 1:
                order, unresolved = order_tied_entries([row["entry_id"] for row in tied], quotient_records)
                tied = [rows[entry] for entry in order]
                tie_groups.extend(unresolved)
            elif len(tied) == 2:
                winner = head_to_head.get(frozenset(r["entry_id"] for r in tied))
                if winner:
                    tied.sort(key=lambda r: r["entry_id"] != winner)
                else:
                    tied.sort(
                        key=lambda r: (r["game_difference"], r["point_difference"]), reverse=True
                    )
                    if (tied[0]["game_difference"], tied[0]["point_difference"]) == (
                        tied[1]["game_difference"],
                        tied[1]["point_difference"],
                    ):
                        tie_groups.append([r["entry_id"] for r in tied])
            elif len(tied) > 2:
                # No invented mini-table or alphabetical tie breaker.
                tie_groups.append([r["entry_id"] for r in tied])
            ordered.extend(tied)
        decisions = [
            d
            for d in snapshot.ranking_decisions
            if d.get("stage_id") == stage_id
            and d.get("pool_id") == pool_id
            and d.get("status") == "approved"
            and d.get("approval_id")
            and str(d.get("reason", "")).strip()
        ]
        decision = decisions[-1] if decisions else None
        manual_needed = exclusions or missing or (bool(tie_groups) and not incomplete)
        if decision and not _approval_current(snapshot, "ranking", decision):
            issue(
                "APPROVAL_STALE",
                "Manual ranking requires a current approval action bound to the exact decision.",
            )
            decision = None
        if decision and decision.get("basis_hash") != basis:
            issue(
                "APPROVAL_STALE",
                "Manual ranking approval is stale after result, exclusion, or rule changes.",
            )
            decision = None
        if decision and not rule_problem and not pending_results:
            order = decision.get("ordered_entry_ids", [])
            if len(order) == len(rows) and set(order) == set(rows):
                ordered = [rows[e] for e in order]
                status = "final"
            else:
                issue(
                    "RANKING_MANUAL_REQUIRED",
                    "Manual decision must contain every group entry exactly once.",
                )
                status = "manual_review"
        elif manual_needed or rule_problem:
            status = "manual_review"
            issue(
                "RANKING_MANUAL_REQUIRED",
                "Incomplete round robin or unresolved tie requires a reasoned, content-bound ranking decision.",
            )
        if status == "final":
            for rank, row in enumerate(ordered, 1):
                row["rank"] = rank
        all_rows.extend(ordered)
        reports.append(
            {
                "pool_id": pool_id,
                "status": status,
                "standings": ordered,
                "tie_groups": tie_groups,
                "incomplete": incomplete,
            }
        )
    overall = (
        "final"
        if all(g["status"] == "final" for g in reports)
        else (
            "manual_review"
            if any(g["status"] == "manual_review" for g in reports)
            else "provisional"
        )
    )
    return {
        "stage_id": stage_id,
        "basis_hash": basis,
        "status": overall,
        "standings": all_rows,
        "groups": reports,
        "issues": all_issues,
    }
