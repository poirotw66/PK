"""Reviewed file handoffs between the local repository and the private Site."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .domain.formats import pending_format_divisions
from .domain.match_rules import effective_match_rules, is_championship
from .domain.models import Assignment, Snapshot, canonical_hash, new_id, stable_id, utcnow
from .domain.rankings import compute_rankings, import_results, resolve_side
from .domain.rules import refresh_rule_snapshots
from .scheduling.constraints import (
    build_context,
    is_court_locked,
    is_time_locked,
    minute,
    ranking_dependencies,
    rest_minutes,
    round_robin_match_groups,
    timestamp,
    validate_schedule,
)
from .storage.repository import Repository


def _receipt(snapshot: Snapshot) -> dict | None:
    return next((c for c in reversed(snapshot.changes) if c.get("kind") == "cloud_return"), None)


def _result(snapshot: Snapshot, match: Any) -> dict | None:
    result = next(
        (
            r
            for r in reversed(snapshot.results)
            if r.get("match_id") == match.id and r.get("status") == "confirmed"
        ),
        None,
    )
    if result is None:
        return None
    if result.get("outcome") not in {"played", "walkover", "retired", "double_forfeit"}:
        raise ValueError("此結果類型尚不能交接雲端。")
    first, second = resolve_side(snapshot, match.side_a), resolve_side(snapshot, match.side_b)
    if result.get("outcome") != "double_forfeit" and result.get("winner_id") not in {first, second}:
        raise ValueError("成績勝方與對戰選手不一致。")
    rules = effective_match_rules(snapshot, match)
    return {
        "rules": {
            "best_of": rules.get("best_of_games"),
            "target": rules.get("points_to_win_game"),
            "margin": 2,
        },
        "winner": None
        if result.get("winner_id") is None
        else "a"
        if result["winner_id"] == first
        else "b",
        **({"outcome": result["outcome"]} if result["outcome"] != "played" else {}),
        "games": deepcopy(result["games"]),
    }


def _cloud_pool_basis(state: dict, pool: dict) -> str:
    matches = {match["id"]: match for match in state["matches"]}
    rows = [
        {"id": mid, "status": matches[mid]["status"], "result": matches[mid].get("result")}
        for mid in sorted(pool["match_ids"])
    ]
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _allowed_outcomes(rules: dict) -> list[str]:
    allowed = ["played"]
    for outcome, key in (
        ("walkover", "walkover_rule"),
        ("retired", "retirement_rule"),
        ("double_forfeit", "double_forfeit_rule"),
    ):
        special = rules.get(key)
        points = (
            ("side_a_points", "side_b_points")
            if outcome == "double_forfeit"
            else ("winner_points", "loser_points")
        )
        denominator = rules.get("ranking_denominator_rule")
        if (
            isinstance(special, dict)
            and all(
                type(special.get(name)) is bool
                for name in ("include_in_rankings", "counts_as_played")
            )
            and all(
                type(special.get(name)) in {int, float}
                and math.isfinite(special[name])
                and special[name] >= 0
                for name in points
            )
            and isinstance(denominator, dict)
            and type(denominator.get(outcome)) is bool
            and (outcome == "retired" or not special["counts_as_played"])
        ):
            allowed.append(outcome)
    return allowed


def _encouragement_sources(snapshot: Snapshot, exported_match_ids: set[str]) -> dict:
    names = {p.id: p.display_name for p in snapshot.participants}
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    entries = {e.id: e for e in snapshot.entries}
    divisions = []
    for division in snapshot.divisions:
        plan = division.format_plan or {}
        if (
            plan.get("template_id") != "single_elimination"
            or plan.get("encouragement_enabled") is False
        ):
            continue
        stages = [
            stage
            for stage in snapshot.stages
            if stage.division_id == division.id
            and stage.type == "elimination"
            and stage.stage_key != "encouragement"
        ]
        if not stages:
            continue
        stage = min(stages, key=lambda row: (row.order, row.id))
        first_round = []
        for match in sorted(snapshot.matches, key=lambda row: (row.bracket_position or 0, row.id)):
            if (
                match.stage_id != stage.id
                or match.round != 1
                or match.kind != "competitive"
                or match.id not in exported_match_ids
            ):
                continue
            if match.side_a.type != "entry" or match.side_b.type != "entry":
                continue
            sides = {}
            for key, side in (("side_a", match.side_a), ("side_b", match.side_b)):
                entry = entries[side.entry_id]
                player = registrations[entry.registration_id]
                sides[key] = {"entry_id": entry.id, "player_id": player, "name": names[player]}
            first_round.append({"match_id": match.id, **sides})
        if first_round:
            divisions.append(
                {
                    "division_id": division.id,
                    "division": division.name,
                    "stage_id": stage.id,
                    "first_round": first_round,
                }
            )
    return {"source_revision": snapshot.id, "divisions": divisions}


def _schedule(snapshot: Snapshot, *, validate: bool = True) -> dict:
    if pending_format_divisions(snapshot):
        raise ValueError("請先完成各組賽制、抽籤及籤表確認，再匯出雲端賽程。")
    context, issues = build_context(snapshot)
    if context is None or issues:
        raise ValueError("賽程設定尚未完整，無法匯出雲端賽程。")
    if (
        context.participant_scope != "confirmed_only"
        or context.minimum_rest_minutes != 60
        or context.max_matches_per_day != 5
        or str(context.zone) != "Asia/Taipei"
    ):
        raise ValueError("現有雲端後台需要已確定選手、休息 60 分鐘、每日最多 5 場及台北時區。")
    errors = [i.message for i in validate_schedule(snapshot) if i.severity == "error"]
    if errors and validate:
        raise ValueError("賽程未通過檢查：" + "；".join(errors)[:1800])
    assignments = {a.match_id: a for a in snapshot.assignments}
    names = {p.id: p.display_name for p in snapshot.participants}
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    entry_names = {e.id: names[registrations[e.registration_id]] for e in snapshot.entries}
    divisions = {d.id: d.name for d in snapshot.divisions}
    stages = {s.id: s for s in snapshot.stages}
    groups = {
        mid: str(i)
        for i, mids in enumerate(round_robin_match_groups(snapshot, context.matches))
        for mid in mids
    }
    ranks = ranking_dependencies(snapshot, context)

    def side_name(side: Any) -> str:
        entry = resolve_side(snapshot, side)
        if entry:
            return entry_names[entry]
        if side.type in {"match_winner", "match_loser"}:
            prior = next((m for m in snapshot.matches if m.id == side.match_id), None)
            label = (prior.display_code or prior.id[:8]) if prior else "來源"
            return f"{label}場{'勝者' if side.type == 'match_winner' else '敗者'}"
        return "對手待確認"

    def slot(side: Any) -> dict:
        if side.type == "pool_rank":
            return {"kind": "pool_rank", "pool_id": side.pool_id, "rank": side.rank}
        if side.type in {"match_winner", "match_loser"}:
            return {
                "kind": "winner" if side.type == "match_winner" else "loser",
                "match_id": side.match_id,
            }
        entry = resolve_side(snapshot, side)
        return {"kind": "entry", "entry_id": entry} if entry else {"kind": "pending"}

    cloud_draws = [
        deepcopy(draw["cloud_record"])
        for draw in snapshot.draws
        if draw.get("kind") == "encouragement"
        and draw.get("status") == "approved"
        and draw.get("cloud_record")
    ]
    cloud_matches = {mid: draw for draw in cloud_draws for mid in draw["match_ids"]}
    matches = []
    for mid, match in context.matches.items():
        if match.status not in {"planned", "ready", "completed"}:
            raise ValueError("請先結束正在進行的比賽，再交接賽程。")
        timing, assignment = context.timing[mid], assignments.get(mid)
        if assignment is None:
            raise ValueError("仍有未排場次，請先完成排程。")
        clock = assignment.start_at.astimezone(context.zone).strftime("%H:%M")
        if not "08:00" <= clock <= "17:30":
            raise ValueError("現有雲端後台只接受 08:00 至 17:30 的開賽時間，請先調整排程。")
        dependencies = []
        for prior in sorted(context.dependencies.get(mid, set())):
            lag = (
                max(
                    timing["ranking_confirmation_minutes"],
                    context.timing[prior]["ranking_confirmation_minutes"],
                )
                if prior in ranks[mid]
                else 0
            )
            if context.advances_from(mid, prior):
                lag = max(lag, rest_minutes(context, prior, mid))
            dependencies.append(
                {"id": prior, "lag": max(lag, context.deferred_lags.get(mid, {}).get(prior, 0))}
            )
        scoring = effective_match_rules(snapshot, match)
        if (
            scoring.get("best_of_games") not in {3, 5}
            or scoring.get("points_to_win_game") not in {11, 15}
            or scoring.get("win_by_points") != 2
            or scoring.get("point_cap_mode") != "none"
        ):
            raise ValueError("雲端計分僅支援三／五局、11／15 分、領先兩分且無上限。")
        stage = stages[match.stage_id]
        row: dict[str, Any] = {
            "id": mid,
            "code": match.display_code or mid[:8],
            "division": divisions[match.division_id],
            "stage": stage.label
            or {"placement": "名次賽", "elimination": "淘汰賽", "round_robin": "循環賽"}[
                stage.type
            ],
            "group": groups.get(mid),
            "championship": is_championship(snapshot, match),
            "side_a": side_name(match.side_a),
            "side_b": side_name(match.side_b),
            "slots": {"a": slot(match.side_a), "b": slot(match.side_b)},
            "entry_ids": {
                "a": resolve_side(snapshot, match.side_a),
                "b": resolve_side(snapshot, match.side_b),
            },
            "status": match.status,
            "allowed_outcomes": list(
                dict.fromkeys(
                    _allowed_outcomes(scoring)
                    + (
                        ["walkover"]
                        if snapshot.config.get("runtime", {}).get("coach_competition_rules")
                        else []
                    )
                )
            ),
            "scoring_rules": {
                "best_of": scoring["best_of_games"],
                "target": scoring["points_to_win_game"],
                "margin": 2,
            },
            "players": sorted(context.candidates.get(mid, set())),
            "play": timing["estimated_play_minutes"],
            "buffer": timing["court_buffer_minutes"],
            "rest": timing["minimum_player_rest_minutes"],
            "transfer": timing["court_transfer_minutes"],
            "dependencies": dependencies,
            "ready_after": context.deferred_ready.get(mid),
            "lock_time": is_time_locked(assignment),
            "lock_court": is_court_locked(assignment),
            "assignment": {"start": minute(assignment.start_at), "court": assignment.court_id},
        }
        if mid in cloud_matches:
            draw = cloud_matches[mid]
            row["encouragement"] = {
                "draw_id": draw["id"],
                "division_id": draw["division_id"],
                "stage_id": match.stage_id,
                "round": match.round,
                "position": match.bracket_position,
            }
            row["allowed_outcomes"] = ["played", "walkover"]
            for key, side in (("a", match.side_a), ("b", match.side_b)):
                if side.type == "match_loser":
                    row["slots"][key] = {"kind": "entry", "entry_id": resolve_side(snapshot, side)}
                elif side.type == "match_winner" and not resolve_side(snapshot, side):
                    prior = context.matches[side.match_id]
                    row["side_" + key] = f"{prior.display_code}勝者"
        if len(row["players"]) > 2:
            raise ValueError("雲端交接僅支援已確定的選手，不支援預留所有可能晉級者。")
        result = _result(snapshot, match)
        if result:
            row["result"] = result
        if match.status == "completed" and not result:
            raise ValueError("已完賽場次缺少已確認成績，不能交接。")
        matches.append(row)
    if not matches or len(matches) > 1000:
        raise ValueError("雲端賽程需包含 1 至 1000 場比賽。")
    pools: list[dict[str, Any]] = []
    for pool in snapshot.pools:
        mids = [
            m.id
            for m in context.matches.values()
            if m.stage_id == pool.stage_id
            and all(resolve_side(snapshot, side) in pool.entry_ids for side in (m.side_a, m.side_b))
        ]
        pools.append(
            {
                "id": pool.id,
                "stage_id": pool.stage_id,
                "label": (
                    stages[pool.stage_id].label or divisions[stages[pool.stage_id].division_id]
                )
                + " / "
                + pool.label,
                "entry_ids": pool.entry_ids,
                "match_ids": mids,
            }
        )
    decisions = []
    for cloud_pool in pools:
        report = next(
            (
                group
                for group in compute_rankings(snapshot, cloud_pool["stage_id"])["groups"]
                if group["pool_id"] == cloud_pool["id"]
            ),
            None,
        )
        if report and report["status"] == "final":
            decisions.append(
                {
                    "stage_id": cloud_pool["stage_id"],
                    "pool_id": cloud_pool["id"],
                    "entry_ids": [row["entry_id"] for row in report["standings"]],
                    "basis": _cloud_pool_basis({"matches": matches}, cloud_pool),
                }
            )
    return {
        "format": "pk-schedule-v1",
        **({"encouragement_draws": cloud_draws} if cloud_draws else {}),
        **(
            {
                "encouragement_sources": _encouragement_sources(
                    snapshot, {m["id"] for m in matches}
                ),
                "coach_profile": {
                    "version": 1,
                    "championship_ids": sorted(m["id"] for m in matches if m["championship"]),
                },
            }
            if snapshot.config.get("runtime", {}).get("coach_competition_rules")
            else {}
        ),
        "entries": [
            {"id": entry.id, "player_id": registrations[entry.registration_id]}
            for entry in snapshot.entries
        ],
        "advancement_pools": pools,
        "advancement_decisions": decisions,
        "source_revision": snapshot.id,
        "name": snapshot.config.get("event", {}).get("name") or "壁球賽事",
        "timezone": "Asia/Taipei",
        "dates": snapshot.config["event"]["event_dates"],
        "grid": context.grid,
        "anchors": {day.isoformat(): value for day, value in context.anchors.items()},
        "first_start": "08:00",
        "last_start": "17:30",
        "minimum_rest": 60,
        "daily_limit": 5,
        "participant_scope": "confirmed_only",
        "keep_groups_same_day": bool(
            (snapshot.config.get("runtime") or {}).get("keep_round_robin_groups_same_day")
        ),
        "courts": [
            {
                "id": court["id"],
                "name": court["label"],
                "windows": [{"start": a, "end": b} for a, b in context.windows[court["id"]]],
            }
            for court in snapshot.config.get("courts", [])
        ],
        "players": [
            {"id": p.id, "name": p.display_name, "rest": p.minimum_rest_minutes or 0}
            for p in snapshot.participants
        ],
        "matches": matches,
    }


def export_advancement_sources(repo: Repository) -> dict:
    """Export source metadata from the exact original cloud lineage, without writes."""
    current = repo.load()
    receipt = _receipt(current)
    source = repo.load(receipt["source_revision"]) if receipt else current
    return _schedule(source)


def export_cloud_schedule(repo: Repository) -> dict:
    """Produce initial import or a compare-and-swap update after cloud return."""
    snapshot = repo.load()
    receipt = _receipt(snapshot)
    state = _schedule(snapshot)
    if receipt is None:
        if any(c.get("kind") == "live_match_event" for c in snapshot.changes):
            raise ValueError("已有地端現場操作，請先完成資料核對再首次交接雲端。")
        return state
    # Results can resolve later-round display names locally. A replan must retain
    # the cloud's original identity contract rather than silently publishing those
    # derived identities as an assignment-only update.
    ancestor = snapshot
    while ancestor.parent_id != receipt["local_revision"]:
        if ancestor.parent_id is None:
            raise ValueError("找不到取回版本的地端歷程。")
        ancestor = repo.load(ancestor.parent_id)
    baseline = _schedule(ancestor)

    def immutable(schedule: dict) -> dict:
        value = deepcopy(schedule)
        value.pop("source_revision")
        if "encouragement_sources" in value:
            value["encouragement_sources"].pop("source_revision", None)
        for match in value["matches"]:
            match.pop("assignment")
            match.pop("allowed_outcomes", None)
        return value

    current_contract = immutable(state)
    baseline_contract = immutable(baseline)
    reviewed_policy = bool(state.get("coach_profile")) and not baseline.get("coach_profile")
    if reviewed_policy:
        comparable = deepcopy(current_contract)
        comparable.pop("coach_profile", None)
        if "encouragement_sources" not in baseline_contract:
            comparable.pop("encouragement_sources", None)
        prior_rows = {row["id"]: row for row in baseline_contract["matches"]}
        for row in comparable["matches"]:
            old = prior_rows.get(row["id"], {})
            if row.get("scoring_rules") != old.get("scoring_rules"):
                prior_rules = old.get("scoring_rules", {})
                if (
                    not row.get("championship")
                    or old.get("status") in {"completed", "in_progress"}
                    or old.get("result")
                    or prior_rules.get("best_of") != 3
                    or row["scoring_rules"] != {**prior_rules, "best_of": 5}
                ):
                    raise ValueError("競賽規則交接只能將尚未開賽的冠軍賽由三局改為五局。")
                row["scoring_rules"] = deepcopy(prior_rules)
        if comparable != baseline_contract:
            raise ValueError("競賽規則交接包含其他不可修改的資料。")
    elif current_contract != baseline_contract:
        raise ValueError("地端重排交接只能調整未開賽時間與場地，不能改名單、賽制或成績。")
    policy_rows = {m["id"]: m for m in state["matches"]}
    profile = deepcopy(state.get("coach_profile"))
    positions = {m["id"]: m["assignment"] for m in state["matches"]}
    policies = {m["id"]: m["allowed_outcomes"] for m in state["matches"]}
    state = deepcopy(receipt["state"])
    state["source_revision"] = snapshot.id
    if "encouragement_sources" in state:
        state["encouragement_sources"]["source_revision"] = snapshot.id
    if reviewed_policy:
        state["coach_profile"] = profile
    for match in state["matches"]:
        if reviewed_policy:
            match["championship"] = policy_rows[match["id"]]["championship"]
            match["scoring_rules"] = deepcopy(policy_rows[match["id"]]["scoring_rules"])
        position = positions[match["id"]]
        if match["status"] == "completed" and position != match["assignment"]:
            raise ValueError("已完賽場次不能重新安排。")
        match["assignment"] = position
        match["allowed_outcomes"] = policies[match["id"]]
    payload = {
        "format": "pk-local-update-v1",
        "base_cloud_revision": receipt["cloud_revision"],
        "base_source_revision": receipt["source_revision"],
        "origin": receipt["origin"],
        "base_sequence": receipt["sequence"],
        "state": state,
    }
    return {**payload, "sha256": canonical_hash(payload)}


def _check_envelope(envelope: Any) -> None:
    fields = {
        "format",
        "source_revision",
        "cloud_revision",
        "origin",
        "exported_at",
        "sequence",
        "state",
        "live",
        "sha256",
    }
    if not isinstance(envelope, dict) or set(envelope) != fields:
        raise ValueError("請選擇雲端後台下載的完整回傳檔。")
    payload = {k: v for k, v in envelope.items() if k != "sha256"}
    if envelope["format"] != "pk-cloud-return-v1" or canonical_hash(payload) != envelope["sha256"]:
        raise ValueError("回傳檔格式或檢查碼不正確。")
    for field in ("source_revision", "cloud_revision", "origin", "exported_at"):
        if not isinstance(envelope[field], str) or not envelope[field]:
            raise ValueError("回傳檔缺少來源識別或版本。")
    origin = urlsplit(envelope["origin"])
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path
        or origin.query
        or origin.fragment
    ):
        raise ValueError("回傳檔的網站來源不正確。")
    exported = datetime.fromisoformat(envelope["exported_at"])
    if exported.utcoffset() is None or exported > utcnow() + timedelta(minutes=5):
        raise ValueError("回傳檔時間無效。")
    if type(envelope["sequence"]) is not int or envelope["sequence"] < 0:
        raise ValueError("回傳檔的同步序號不正確。")
    if not isinstance(envelope["state"], dict) or not isinstance(envelope["live"], dict):
        raise TypeError("回傳檔的賽事資料不正確。")
    if set(envelope["live"]) != {"starts", "actual_courts"} or any(
        not isinstance(value, dict) for value in envelope["live"].values()
    ):
        raise ValueError("回傳檔的現場紀錄不正確。")


def _replay_cloud_advancement(
    working: Snapshot, state: dict, expected: dict, results: list[dict], actor: str, reason: str
) -> Snapshot:
    from .domain.rankings import ranking_basis
    from .domain.revisions import apply_patch, approve
    from .domain.validation import target_basis

    pools = {pool["id"]: pool for pool in state.get("advancement_pools", [])}
    decisions = state.get("advancement_decisions", [])
    if (
        not isinstance(decisions, list)
        or len(decisions) > len(pools)
        or len({item.get("pool_id") for item in decisions if isinstance(item, dict)})
        != len(decisions)
    ):
        raise ValueError("回傳人工名次格式不正確。")
    prior = {item["pool_id"]: item for item in expected.get("advancement_decisions", [])}
    pending_decisions = []
    cloud_matches = {match["id"]: match for match in state["matches"]}
    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != {
            "stage_id",
            "pool_id",
            "entry_ids",
            "basis",
        }:
            raise ValueError("回傳人工名次欄位不正確。")
        pool = pools.get(decision["pool_id"])
        if prior.get(decision["pool_id"]) == decision:
            continue
        if (
            not pool
            or decision["stage_id"] != pool["stage_id"]
            or len(decision["entry_ids"]) != len(pool["entry_ids"])
            or set(decision["entry_ids"]) != set(pool["entry_ids"])
            or decision["basis"] != _cloud_pool_basis(state, pool)
        ):
            raise ValueError("回傳人工名次已過期或缺少組內選手。")
        if any(
            cloud_matches[mid]["status"] != "completed" or not cloud_matches[mid].get("result")
            for mid in pool["match_ids"]
        ):
            raise ValueError("小組成績未完整確認，不能回傳核定名次。")
        if prior.get(pool["id"]) != decision:
            pending_decisions.append(decision)
    remaining = list(results)
    while remaining or pending_decisions:
        progress = False
        for decision in list(pending_decisions):
            pool = pools[decision["pool_id"]]
            if any(result["match_id"] in pool["match_ids"] for result in remaining):
                continue
            if not all(
                any(match.id == mid and match.status == "completed" for match in working.matches)
                for mid in pool["match_ids"]
            ):
                continue
            # Existing played downstream identity may never be reinterpreted.
            if any(
                match.status in {"in_progress", "completed"}
                and any(
                    side.type == "pool_rank" and side.pool_id == pool["id"]
                    for side in (match.side_a, match.side_b)
                )
                for match in working.matches
            ):
                raise ValueError("小組晉級場次已開賽，不能回傳新的人工名次。")
            working, _ = apply_patch(
                working,
                {
                    "base_revision_id": working.id,
                    "actor_id": actor,
                    "reason": reason,
                    "operations": [
                        {
                            "op": "set_ranking_decision",
                            "stage_id": decision["stage_id"],
                            "pool_id": decision["pool_id"],
                            "ordered_entry_ids": decision["entry_ids"],
                            "basis_hash": ranking_basis(working, decision["stage_id"]),
                        }
                    ],
                },
            )
            value = working.ranking_decisions[-1]
            working, _ = approve(
                working, "ranking:" + value["id"], target_basis(value), actor, reason
            )
            pending_decisions.remove(decision)
            progress = True
        for result in list(remaining):
            match = next(match for match in working.matches if match.id == result["match_id"])
            if not resolve_side(working, match.side_a) or not resolve_side(working, match.side_b):
                continue
            import_results(working, {"results": [result], "actor_id": actor, "reason": reason})
            remaining.remove(result)
            progress = True
        if not progress:
            raise ValueError("回傳成績仍有未確認的晉級或小組名次來源。")
    return working


def _review_return_coach_profile(working: Snapshot, state: dict, expected: dict) -> None:
    """Accept only the explicitly reviewed policy for the canonical final identities."""
    profile = state.get("coach_profile")
    if profile is None:
        if expected.get("coach_profile") is not None:
            raise ValueError("回傳檔不能移除已確認的競賽規則。")
        return
    match_ids = {m["id"] for m in expected["matches"]}
    championships = sorted(
        m.id for m in working.matches if m.id in match_ids and is_championship(working, m)
    )
    if (
        not isinstance(profile, dict)
        or set(profile) != {"version", "championship_ids"}
        or type(profile["version"]) is not int
        or profile["version"] != 1
        or not isinstance(profile["championship_ids"], list)
        or any(not isinstance(mid, str) for mid in profile["championship_ids"])
        or len(set(profile["championship_ids"])) != len(profile["championship_ids"])
        or sorted(profile["championship_ids"]) != championships
    ):
        raise ValueError("教練競賽規則的冠軍場次與地端正式籤表不一致。")
    working.config.setdefault("runtime", {})["coach_competition_rules"] = True
    refresh_rule_snapshots(working)
    expected["coach_profile"] = deepcopy(profile)
    local_matches = {match.id: match for match in working.matches}
    for row in expected["matches"]:
        match = local_matches[row["id"]]
        row["championship"] = is_championship(working, match)
        row["allowed_outcomes"] = list(
            dict.fromkeys(row.get("allowed_outcomes", ["played"]) + ["walkover"])
        )
        scoring = effective_match_rules(working, match)
        row["scoring_rules"] = {
            "best_of": scoring["best_of_games"],
            "target": scoring["points_to_win_game"],
            "margin": 2,
        }


def _approve_cloud_draw(snapshot: Snapshot, record: dict, actor: str, reason: str) -> None:
    from .domain.encouragement_draws import CLOUD_ALGORITHM, _output
    from .domain.validation import target_basis

    record["output"] = _output(
        snapshot, record["division_id"], record["random_seed"], CLOUD_ALGORITHM
    )
    approval = {
        "id": new_id(),
        "action": "approve",
        "target_type": "encouragement_draw",
        "target_id": record["id"],
        "basis_hash": target_basis(record),
        "actor_id": actor,
        "reason": reason,
        "created_at": utcnow().isoformat(),
    }
    record["approval_id"] = approval["id"]
    snapshot.approvals.append(approval)


def _review_return_encouragement(
    working: Snapshot, state: dict, expected: dict, actor: str, reason: str
) -> set[str]:
    """Reproduce additions from approved canonical first-round identities, never names."""
    from .domain.encouragement_draws import CLOUD_ALGORITHM, KIND, _output, _record_geometry

    records = state.get("encouragement_draws", [])
    prior = expected.get("encouragement_draws", [])
    if not isinstance(records, list) or len(records) > len(working.divisions):
        raise ValueError("鼓勵賽抽籤紀錄格式不正確。")
    fields = {
        "id",
        "division_id",
        "division",
        "source_stage_id",
        "source_match_ids",
        "seed",
        "basis",
        "entry_ids",
        "bracket_entry_ids",
        "match_ids",
        "generated_at",
    }
    if any(not isinstance(row, dict) or set(row) != fields for row in records):
        raise ValueError("鼓勵賽抽籤紀錄欄位不正確。")
    if len({row["division_id"] for row in records}) != len(records):
        raise ValueError("同組別不能重複產生鼓勵籤表。")
    by_division = {row["division_id"]: row for row in records}
    if any(by_division.get(row["division_id"]) != row for row in prior):
        raise ValueError("既有鼓勵籤表不能刪除或更改。")
    additions = [row for row in records if row not in prior]
    if not additions:
        return set()
    if not working.config.get("runtime", {}).get("coach_competition_rules"):
        raise ValueError("請先確認教練競賽規則，才能取回鼓勵籤表。")
    if not isinstance(state.get("entries"), list):
        raise TypeError("鼓勵籤表取回需要正式選手識別碼。")
    if not isinstance(state.get("matches"), list) or any(
        not isinstance(row, dict) for row in state["matches"]
    ):
        raise ValueError("回傳場次格式不正確。")
    after = {row.get("id"): row for row in state["matches"]}
    before = {row["id"]: row for row in expected["matches"]}
    # Old Sites attached no identity registry to original matches. New qualified
    # entries can still be proved by canonical source IDs, without backfilling or
    # reinterpreting any original row.
    legacy_rows = bool(before) and all("slots" not in after.get(mid, {}) for mid in before)
    if legacy_rows:
        for row in expected["matches"]:
            row.pop("slots", None)
            row.pop("entry_ids", None)
        expected["entries"] = []
        for key in ("advancement_pools", "advancement_decisions"):
            if key not in state:
                expected.pop(key, None)
    bindings = {row["id"]: row for row in expected.get("entries", [])}
    registration_people = {row.id: row.participant_id for row in working.registrations}
    canonical_bindings = {
        entry.id: {"id": entry.id, "player_id": registration_people[entry.registration_id]}
        for entry in working.entries
    }
    qualification = working.model_copy(deep=True)
    local = {match.id: match for match in qualification.matches}
    new_ids: set[str] = set()
    new_records = []
    for cloud in additions:
        division = next((d for d in qualification.divisions if d.id == cloud["division_id"]), None)
        stage_id = stable_id("placement-stage", cloud["division_id"], KIND)
        if (
            not division
            or cloud["division"] != division.name
            or cloud["id"] != stage_id
            or type(cloud["seed"]) is not int
            or not 0 <= cloud["seed"] <= 0xFFFFFFFF
        ):
            raise ValueError("鼓勵籤表的組別或抽籤種子不正確。")
        if any(
            draw.get("kind") == KIND
            and draw.get("division_id") == division.id
            and draw.get("status") != "superseded"
            for draw in working.draws
        ):
            raise ValueError("地端已有鼓勵籤表，不能以雲端紀錄取代。")
        instant = datetime.fromisoformat(cloud["generated_at"])
        if instant.utcoffset() is None or instant > utcnow() + timedelta(minutes=5):
            raise ValueError("鼓勵賽抽籤時間不正確。")
        source_stage = stable_id("stage", division.id, "elimination")
        sources = sorted(
            match.id
            for match in qualification.matches
            if match.stage_id == source_stage and match.round == 1 and match.kind == "competitive"
        )
        if (
            cloud["source_stage_id"] != source_stage
            or sorted(cloud["source_match_ids"]) != sources
            or len(set(cloud["source_match_ids"])) != len(sources)
        ):
            raise ValueError("鼓勵賽來源場次不符合地端正式籤表。")
        if cloud["basis"] != _cloud_pool_basis(state, {"match_ids": sources}):
            raise ValueError("鼓勵賽資格依據與首輪成績不一致。")
        for mid in sources:
            row, original = after.get(mid), before.get(mid)
            if (
                not row
                or not original
                or any(
                    row.get(key) != original.get(key)
                    for key in original
                    if key not in {"assignment", "status", "result"}
                )
            ):
                raise ValueError("鼓勵賽首輪身分、規則或對戰不能更改。")
            result = row.get("result")
            if (
                row.get("status") != "completed"
                or not isinstance(result, dict)
                or result.get("rules") != row["scoring_rules"]
                or set(result) - {"rules", "winner", "games", "outcome"}
            ):
                raise ValueError("首輪成績尚未齊全，不能取回鼓勵籤表。")
            if result != original.get("result"):
                winner = result.get("winner")
                match = local[mid]
                if winner not in {"a", "b", None}:
                    raise ValueError("首輪成績勝方不正確。")
                import_results(
                    qualification,
                    {
                        "results": [
                            {
                                "match_id": mid,
                                "outcome": result.get("outcome", "played"),
                                "status": "confirmed",
                                "winner_id": resolve_side(
                                    qualification, match.side_a if winner == "a" else match.side_b
                                )
                                if winner
                                else None,
                                "games": result["games"],
                            }
                        ],
                        "actor_id": actor,
                        "reason": reason,
                    },
                )
        output = _output(qualification, division.id, cloud["seed"], CLOUD_ALGORITHM)
        if (
            cloud["entry_ids"] != output["entry_ids"]
            or cloud["bracket_entry_ids"] != output["bracket_entry_ids"]
        ):
            raise ValueError("鼓勵籤表未重現首輪合格敗者的正式抽籤結果。")
        for entry_id in output["entry_ids"]:
            bindings[entry_id] = canonical_bindings[entry_id]
        record = {
            "id": stage_id,
            "kind": KIND,
            "division_id": division.id,
            "random_seed": cloud["seed"],
            "output": output,
            "cloud_record": deepcopy(cloud),
            "actor_id": actor,
            "reason": reason,
            "created_at": cloud["generated_at"],
            "status": "approved",
        }
        stage, matches = _record_geometry(record)
        if cloud["match_ids"] != [match.id for match in matches] or set(cloud["match_ids"]) & set(
            before
        ):
            raise ValueError("鼓勵籤表場次與正式淘汰圖不一致。")
        # A fixed stage rule set makes future local replans reproduce the cloud
        # match duration/scoring while every pre-existing rule snapshot stays intact.
        stage.rule_set_id = stable_id("cloud-encouragement-rules", division.id)
        rules = {
            "match_rules": {
                "best_of_games": 3,
                "points_to_win_game": 11,
                "win_by_points": 2,
                "point_cap_mode": "none",
                "point_cap": None,
            },
            "timing": {
                "estimated_play_minutes": 10,
                "court_buffer_minutes": 5,
                "minimum_player_rest_minutes": 60,
                "court_transfer_minutes": 0,
            },
        }
        for target in (qualification, working):
            target.config.setdefault("rule_sets", {})[stage.rule_set_id] = deepcopy(rules)
            if matches:
                target.stages.append(stage.model_copy(deep=True))
                target.matches.extend(match.model_copy(deep=True) for match in matches)
            for match in matches:
                row = after.get(match.id, {})
                position = row.get("assignment")
                if (
                    not isinstance(position, dict)
                    or set(position) != {"start", "court"}
                    or type(position["start"]) is not int
                    or not isinstance(position["court"], str)
                ):
                    raise ValueError("新增鼓勵場次缺少有效場地與參考時間。")
                start = timestamp(position["start"])
                target.assignments.append(
                    Assignment(
                        match_id=match.id,
                        court_id=position["court"],
                        start_at=start,
                        play_end_at=start + timedelta(minutes=10),
                        court_release_at=start + timedelta(minutes=15),
                    )
                )
            target.draws.append(deepcopy(record))
            refresh_rule_snapshots(target, {match.id for match in matches})
        _approve_cloud_draw(qualification, qualification.draws[-1], actor, reason)
        new_records.append(record)
        new_ids.update(cloud["match_ids"])
    derived = _schedule(qualification, validate=False)
    expected_rows = {row["id"]: row for row in derived["matches"]}
    for mid in new_ids:
        row = expected_rows[mid]
        # Dependency order is cosmetic; the exact set and lags remain canonical.
        if sorted(row["dependencies"], key=lambda value: value["id"]) == sorted(
            after[mid].get("dependencies", []), key=lambda value: value["id"]
        ):
            row["dependencies"] = deepcopy(after[mid]["dependencies"])
        expected["matches"].append(row)
    if (
        any(
            not isinstance(row, dict) or set(row) != {"id", "player_id"} for row in state["entries"]
        )
        or len(state["entries"]) != len(bindings)
        or any(bindings.get(row["id"]) != row for row in state["entries"])
    ):
        raise ValueError("鼓勵賽選手識別碼與正式合格敗者不一致。")
    expected["entries"] = deepcopy(state["entries"])
    expected["encouragement_draws"] = deepcopy(records)
    return {record["id"] for record in new_records}


def execute_cloud_return(
    snapshot: Snapshot, repo: Repository, envelope: dict, actor: str, reason: str
) -> tuple[Snapshot, dict]:
    """Preview without writing; OperatorService confirms against the exact revision."""
    _check_envelope(envelope)
    source = envelope["source_revision"]
    receipt = _receipt(snapshot)
    if receipt and receipt["origin"] != envelope["origin"]:
        raise ValueError("回傳檔來自不同的管理網站。")
    if receipt and source == receipt["source_revision"]:
        if snapshot.parent_id != receipt["local_revision"]:
            raise ValueError("地端已在取回後修改，請先將地端版本交接雲端，不能覆蓋。")
        if (
            envelope["sequence"] < receipt["sequence"]
            or datetime.fromisoformat(envelope["exported_at"])
            < datetime.fromisoformat(receipt["exported_at"])
            or any(
                c.get("kind") == "cloud_return"
                and c.get("cloud_revision") == envelope["cloud_revision"]
                and c.get("sequence") == envelope["sequence"]
                for c in snapshot.changes
            )
        ):
            raise ValueError("此雲端版本已取回或早於已取回版本。")
        expected = receipt["state"]
    else:
        if snapshot.id != source:
            raise ValueError("地端版本與雲端原始來源不一致，請開啟原工作區或先完成版本核對。")
        expected = export_cloud_schedule(repo)
        expected = expected.get("state", expected)
    state = envelope["state"]
    expected = deepcopy(expected)
    working = snapshot.model_copy(deep=True)
    _review_return_coach_profile(working, state, expected)
    new_encouragement = _review_return_encouragement(working, state, expected, actor, reason)
    if "encouragement_sources" not in state:
        expected.pop("encouragement_sources", None)
    # Legacy Sites have no explicit source metadata. Preserve their strict contract.
    if "entries" not in state:
        expected = deepcopy(expected)
        for key in ("entries", "advancement_pools", "advancement_decisions"):
            expected.pop(key, None)
        for match in expected["matches"]:
            match.pop("slots", None)
            match.pop("entry_ids", None)
    if not isinstance(state.get("matches"), list) or any(
        not isinstance(row, dict) for row in state["matches"]
    ):
        raise ValueError("回傳場次格式不正確。")
    for match in expected["matches"]:
        received: dict[str, Any] = next(
            (row for row in state["matches"] if row.get("id") == match["id"]), {}
        )
        if "allowed_outcomes" not in received:
            match.pop("allowed_outcomes", None)
        if "championship" not in received:
            match.pop("championship", None)
    if state.get("source_revision") != source or set(state) != set(expected):
        raise ValueError("回傳檔的來源版本或資料契約不一致。")
    if {k: v for k, v in state.items() if k not in {"matches", "advancement_decisions"}} != {
        k: v for k, v in expected.items() if k not in {"matches", "advancement_decisions"}
    }:
        raise ValueError("雲端回傳不能修改賽事規則、名單、場地或身分。")
    if not isinstance(state["matches"], list) or any(
        not isinstance(m, dict) or not isinstance(m.get("id"), str) for m in state["matches"]
    ):
        raise ValueError("回傳場次格式不正確。")
    before = {m["id"]: m for m in expected["matches"]}
    after = {m["id"]: m for m in state["matches"]}
    if set(before) != set(after) or len(after) != len(state["matches"]):
        raise ValueError("雲端回傳不能新增、刪除或重複場次。")
    matches = {m.id: m for m in working.matches}
    assignments = {a.match_id: a for a in working.assignments}
    updates, results, statuses = [], [], []
    mutable = {"assignment", "status", "result"}
    identity_changes = []
    for mid, row in after.items():
        original = before[mid]
        row_mutable = mutable | (
            {"side_a", "side_b", "players", "entry_ids"} if original.get("slots") else set()
        )
        if {k: v for k, v in row.items() if k not in row_mutable} != {
            k: v for k, v in original.items() if k not in row_mutable
        }:
            raise ValueError("雲端回傳不能修改對戰、選手、規則或依賴關係。")
        if row.get("status") not in {"planned", "ready", "completed"}:
            raise ValueError("仍有比賽進行中，請先完成計分再取回。")
        if any(
            row.get(key) != original.get(key)
            for key in ("side_a", "side_b", "players", "entry_ids")
        ):
            if (
                original["status"] in {"completed", "in_progress"}
                or original["lock_time"]
                or original["lock_court"]
            ):
                raise ValueError("已開賽、完賽或鎖定的對戰身分不能更改。")
            identity_changes.append(mid)
        position = row.get("assignment")
        if (
            not isinstance(position, dict)
            or set(position) != {"start", "court"}
            or type(position["start"]) is not int
            or not isinstance(position["court"], str)
        ):
            raise ValueError("回傳場次缺少有效的時間與場地。")
        if position != original["assignment"]:
            start = timestamp(position["start"])
            assignments[mid] = Assignment(
                match_id=mid,
                court_id=position["court"],
                start_at=start,
                play_end_at=start + timedelta(minutes=row["play"]),
                court_release_at=start + timedelta(minutes=row["play"] + row["buffer"]),
                locks=assignments[mid].locks,
            )
            updates.append(
                {
                    "match_id": mid,
                    "code": row["code"],
                    "before": original["assignment"],
                    "after": position,
                }
            )
        result = row.get("result")
        if (row["status"] == "completed") != (result is not None):
            raise ValueError("完賽狀態與成績不一致。")
        if result is not None and result != original.get("result"):
            if (
                not isinstance(result, dict)
                or not {"rules", "winner", "games"} <= set(result)
                or set(result) - {"rules", "winner", "games", "outcome"}
                or result["rules"] != row["scoring_rules"]
                or result.get("outcome", "played")
                not in {"played", "walkover", "retired", "double_forfeit"}
                or result["winner"] not in {"a", "b", None}
                or (result.get("outcome", "played") == "double_forfeit")
                != (result["winner"] is None)
            ):
                raise ValueError("回傳成績格式或計分規則不正確。")
            match = matches[mid]
            winner = (
                (
                    row.get("entry_ids", {}).get(result["winner"])
                    or resolve_side(
                        working, match.side_a if result["winner"] == "a" else match.side_b
                    )
                )
                if result["winner"]
                else None
            )
            results.append(
                {
                    "match_id": mid,
                    "outcome": result.get("outcome", "played"),
                    "status": "confirmed",
                    "winner_id": winner,
                    "games": result["games"],
                }
            )
        if row["status"] != original["status"]:
            statuses.append({"match_id": mid, "before": original["status"], "after": row["status"]})
        if row["status"] != "completed":
            matches[mid].status = row["status"]
    working.assignments = list(assignments.values())
    working = _replay_cloud_advancement(working, state, expected, results, actor, reason)
    # Confirm evidence against the actual imported result records (whose audit IDs
    # are freshly generated), rather than against temporary qualification records.
    for record in working.draws:
        if record.get("id") in new_encouragement:
            _approve_cloud_draw(working, record, actor, reason)
    if identity_changes:
        entry_people = {
            entry.id: next(
                reg.participant_id
                for reg in working.registrations
                if reg.id == entry.registration_id
            )
            for entry in working.entries
        }
        names = {person.id: person.display_name for person in working.participants}
        local_matches = {match.id: match for match in working.matches}
        for mid in identity_changes:
            match, row = local_matches[mid], after[mid]
            ids = {
                "a": resolve_side(working, match.side_a),
                "b": resolve_side(working, match.side_b),
            }
            bound = row.get("entry_ids")
            if not isinstance(bound, dict) or set(bound) != {"a", "b"}:
                raise ValueError("回傳晉級身分格式不正確。")
            people = []
            for key, side in (("a", match.side_a), ("b", match.side_b)):
                entry = bound[key]
                if entry is None:
                    if side.type not in {"match_winner", "match_loser", "pool_rank"} or row.get(
                        "result"
                    ):
                        raise ValueError("固定或已完賽選手不能清除身分。")
                    continue
                if entry != ids[key] or entry not in entry_people:
                    raise ValueError("回傳晉級身分與已確認的來源結果不一致。")
                person = entry_people[entry]
                if row["side_" + key] != names[person]:
                    raise ValueError("回傳晉級姓名與實際選手不一致。")
                people.append(person)
            if len(set(people)) != len(people) or sorted(people) != sorted(row["players"]):
                raise ValueError("回傳晉級實際選手不一致。")
    errors = [
        i.message
        for i in validate_schedule(working, baseline_assignments=snapshot.assignments)
        if i.severity == "error"
    ]
    if errors:
        raise ValueError("回傳賽程有衝突：" + "；".join(errors)[:1800])
    starts, actual_courts = envelope["live"]["starts"], envelope["live"]["actual_courts"]
    if set(starts) != set(actual_courts) or not set(starts) <= set(after):
        raise ValueError("實際開賽紀錄缺少場地或包含未知場次。")
    if receipt:
        for mid, started in receipt["live"]["starts"].items():
            if (
                starts.get(mid) != started
                or actual_courts.get(mid) != receipt["live"]["actual_courts"][mid]
            ):
                raise ValueError("已取回的實際開賽紀錄不能刪除或更改。")
    courts = {c["id"] for c in state["courts"]}
    for mid, started in starts.items():
        if (
            not isinstance(started, str)
            or actual_courts[mid] not in courts
            or after[mid]["status"] != "completed"
        ):
            raise ValueError("實際開賽時間、場地或完賽狀態不正確。")
        instant = datetime.fromisoformat(started)
        if instant.utcoffset() is None or instant > datetime.fromisoformat(envelope["exported_at"]):
            raise ValueError("實際開賽時間須包含時區，且不能晚於雲端匯出時間。")
    working.changes.append(
        {
            "id": new_id(),
            "kind": "cloud_return",
            "actor_id": actor,
            "reason": reason,
            "created_at": utcnow().isoformat(),
            "local_revision": snapshot.id,
            "source_revision": source,
            "cloud_revision": envelope["cloud_revision"],
            "origin": envelope["origin"],
            "sequence": envelope["sequence"],
            "exported_at": envelope["exported_at"],
            "sha256": envelope["sha256"],
            "state": deepcopy(state),
            "live": deepcopy(envelope["live"]),
        }
    )
    return working, {
        "title": "取回雲端賽程與成績",
        "origin": envelope["origin"],
        "source_revision": source,
        "cloud_revision": envelope["cloud_revision"],
        "schedule_changes": updates,
        "result_count": len(results),
        "results": results,
        "status_changes": statuses,
        "actual_starts": deepcopy(envelope["live"]),
        "notice": "確認後建立新的地端版本。重新編排後須使用交接檔核對雲端版本再發布。",
    }
