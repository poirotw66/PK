"""Export a private local workspace into the minimal cloud scheduling contract."""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheduler", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.scheduler / "src"))
    from squash_scheduler.domain.rankings import effective_rules
    from squash_scheduler.operations import OperatorService
    from squash_scheduler.scheduling.constraints import (
        build_context, is_court_locked, is_time_locked, minute,
        ranking_dependencies, round_robin_match_groups,
    )
    from squash_scheduler.storage.repository import Repository

    repo = Repository(args.workspace)
    snapshot = repo.load()
    context, issues = build_context(snapshot)
    if context is None or issues:
        raise SystemExit("The source scheduling context is invalid.")
    if (context.participant_scope != "confirmed_only" or context.minimum_rest_minutes != 60
            or context.max_matches_per_day != 5 or str(context.zone) != "Asia/Taipei"):
        raise SystemExit("This cloud version requires confirmed-only, 60-minute rest, 5/day, Asia/Taipei.")
    service = OperatorService(repo)
    try:
        state = service.state()
    finally:
        service.close()
    if any(row.get("started_at") or row.get("delay_active") for court in state.get("live_board", {}).get("courts", []) for row in court.get("matches", [])):
        raise SystemExit("Import live-state workspaces only after reconciling actual starts and delays locally.")
    rows = {m["id"]: m for m in state["matches"]}
    assignments = {a.match_id: a for a in snapshot.assignments}
    groups = {mid: str(i) for i, mids in enumerate(round_robin_match_groups(snapshot, context.matches)) for mid in mids}
    ranks = ranking_dependencies(snapshot, context)
    matches = []
    for mid, match in context.matches.items():
        row, timing, assignment = rows[mid], context.timing[mid], assignments.get(mid)
        dependencies = []
        for prior in sorted(context.dependencies.get(mid, set())):
            lag = max(timing["ranking_confirmation_minutes"], context.timing[prior]["ranking_confirmation_minutes"]) if prior in ranks[mid] else 0
            dependencies.append({"id": prior, "lag": max(lag, context.deferred_lags.get(mid, {}).get(prior, 0))})
        scoring = effective_rules(snapshot, match.stage_id)
        if (scoring.get("best_of_games") not in {3, 5}
                or scoring.get("points_to_win_game") not in {11, 15}
                or scoring.get("win_by_points") != 2 or scoring.get("point_cap_mode") != "none"):
            raise SystemExit("The cloud scoreboard requires supported no-cap scoring rules.")
        matches.append({
            "id": mid, "code": row["code"], "division": row["division"], "stage": row["stage"],
            "group": groups.get(mid), "side_a": row["side_a"], "side_b": row["side_b"],
            "scoring_rules": {"best_of": scoring["best_of_games"], "target": scoring["points_to_win_game"], "margin": 2},
            "players": sorted(context.candidates.get(mid, set())), "status": match.status,
            "play": timing["estimated_play_minutes"], "buffer": timing["court_buffer_minutes"],
            "rest": timing["minimum_player_rest_minutes"], "transfer": timing["court_transfer_minutes"],
            "dependencies": dependencies, "ready_after": context.deferred_ready.get(mid),
            "lock_time": is_time_locked(assignment) if assignment else False,
            "lock_court": is_court_locked(assignment) if assignment else False,
            "assignment": {"start": minute(assignment.start_at), "court": assignment.court_id} if assignment else None,
        })
    payload = {
        "format": "pk-schedule-v1", "source_revision": snapshot.id, "name": state["name"],
        "timezone": "Asia/Taipei", "dates": state["event_dates"], "grid": context.grid,
        "anchors": {day.isoformat(): value for day, value in context.anchors.items()},
        "first_start": "08:00", "last_start": "17:30", "minimum_rest": 60, "daily_limit": 5,
        "participant_scope": "confirmed_only", "keep_groups_same_day": bool((snapshot.config.get("runtime") or {}).get("keep_round_robin_groups_same_day")),
        "courts": [{"id": court["id"], "name": court["label"], "windows": [{"start": a, "end": b} for a, b in context.windows[court["id"]]]} for court in state["courts"]],
        "players": [{"id": p.id, "name": p.display_name, "rest": p.minimum_rest_minutes or 0} for p in snapshot.participants],
        "matches": matches,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"matches": len(matches), "dates": payload["dates"], "source_revision": snapshot.id}))


if __name__ == "__main__":
    main()
