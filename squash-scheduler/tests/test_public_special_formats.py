"""Reviewed F qualification/draw execution, encouragement and bronze reader linkage."""

import importlib.util
from datetime import timedelta
from pathlib import Path

from test_operator import operator_snapshot

from squash_scheduler.domain.deferred_draws import bind_deferred_execution, realize_deferred_draw
from squash_scheduler.domain.encouragement_draws import apply_draw, propose_draw
from squash_scheduler.domain.rankings import import_results, resolve_side
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis, validate
from squash_scheduler.public_feed import project_snapshot
from squash_scheduler.scheduling import solve_schedule


def fixture(key):
    spec = importlib.util.spec_from_file_location(
        "reader_scenarios", Path(__file__).parents[1] / "examples/build_reader_scenarios.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config, _ = operator_snapshot()
    return module.scenario(
        config.config,
        key,
        9 if key == "f" else 8,
        "虛構特殊賽制",
        "pools_knockout" if key == "f" else "single_elimination",
    )


def score(snapshot, match, winner_a=True):
    rules = match.rule_snapshot["match_rules"]
    needed = rules["best_of_games"] // 2 + 1
    return {
        "match_id": match.id,
        "status": "confirmed",
        "outcome": "played",
        "winner_id": resolve_side(snapshot, match.side_a if winner_a else match.side_b),
        "games": [
            {"side_a": 11, "side_b": 4} if winner_a else {"side_a": 4, "side_b": 11}
            for _ in range(needed)
        ],
    }


def finish_pools(snapshot):
    rr_ids = {s.id for s in snapshot.stages if s.type == "round_robin"}
    import_results(
        snapshot,
        {
            "actor_id": "tester",
            "results": [score(snapshot, m) for m in snapshot.matches if m.stage_id in rr_ids],
        },
    )


def execute_f(snapshot):
    public = project_snapshot(snapshot, "scope")
    firsts = [
        next(row.entry_id for row in group.rows if row.rank == 1) for group in public.standings
    ]
    seconds = [
        next(row.entry_id for row in group.rows if row.rank == 2) for group in public.standings
    ]
    declaration = next(d for d in snapshot.draws if d.get("kind") == "deferred_slots")
    end = max(
        a.play_end_at
        for a in snapshot.assignments
        if any(
            m.id == a.match_id
            and m.stage_id in {s.id for s in snapshot.stages if s.type == "round_robin"}
            for m in snapshot.matches
        )
    )
    execution = realize_deferred_draw(
        snapshot,
        {
            "draw_run_id": declaration["id"],
            "assignments": [
                {"slot_id": str(i), "entry_id": (firsts if i in {1, 3, 6} else seconds).pop(0)}
                for i in range(1, 7)
            ],
            "executed_at": (end + timedelta(minutes=15)).isoformat(),
            "evidence": "Private fictional signed sheet",
        },
        "tester",
        "Simulated actual draw",
    )
    return execution


def test_f_group_final_does_not_bypass_actual_draw_approval_and_activation():
    snapshot = fixture("f")
    assert len(project_snapshot(snapshot, "scope").standings) == 3
    finish_pools(snapshot)
    public = project_snapshot(snapshot, "scope")
    assert all(
        g.status == "final" and sum(row.qualifies for row in g.rows) == 2 for g in public.standings
    )
    slots = [
        side
        for m in public.matches
        for side in (m.side_a, m.side_b)
        if side.source_type == "draw_slot"
    ]
    assert len(slots) == 6 and all(side.entry_id is None for side in slots)
    execution = execute_f(snapshot)
    pending = project_snapshot(snapshot, "scope")
    assert not any(
        side.entry_id
        for m in pending.matches
        for side in (m.side_a, m.side_b)
        if side.source_type == "draw_slot"
    )
    snapshot, _ = approve(
        snapshot,
        f"deferred_draw:{execution['id']}",
        target_basis(execution),
        "reviewer",
        "Confirm simulated signed sheet",
    )
    bind_deferred_execution(snapshot, execution["id"])
    assert solve_schedule(snapshot, time_limit_seconds=5)["status"] in {"FEASIBLE", "OPTIMAL"}
    assert validate(snapshot, "G4")["passed"]
    public = project_snapshot(snapshot, "scope")
    slots = [
        side
        for m in public.matches
        for side in (m.side_a, m.side_b)
        if side.source_type == "draw_slot"
    ]
    assert len({side.entry_id for side in slots}) == 6
    assert all(side.resolution == "confirmed" for side in slots)
    assert "Private fictional signed sheet" not in public.model_dump_json()


def test_encouragement_only_appears_after_confirmation_and_bronze_tracks_semifinal_losers():
    snapshot = fixture("junior")
    division = snapshot.divisions[0]
    first = [m for m in snapshot.matches if m.round == 1 and m.kind == "competitive"]
    import_results(snapshot, {"results": [score(snapshot, m) for m in first]})
    before = project_snapshot(snapshot, "scope")
    proposal = propose_draw(
        snapshot, division.id, 77, "tester", "Reviewed fictional first-round losers"
    )
    assert not any("鼓勵" in stage.name for stage in before.stages)
    assert not any("鼓勵" in stage.name for stage in project_snapshot(snapshot, "scope").stages)
    snapshot, audit = apply_draw(
        snapshot, proposal, proposal["proposal_hash"], "reviewer", "Confirm fictional encouragement"
    )
    public = project_snapshot(snapshot, "scope")
    encouragement = {stage.id for stage in public.stages if "鼓勵" in stage.name}
    assert encouragement
    assert len([m for m in public.matches if m.stage_id in encouragement]) == 3
    assert len(audit["match_ids"]) == 3
    semis = [m for m in snapshot.matches if m.round == 2 and m.kind == "competitive"]
    losers = {resolve_side(snapshot, m.side_b) for m in semis}
    import_results(snapshot, {"results": [score(snapshot, m) for m in semis]})
    public = project_snapshot(snapshot, "scope")
    bronze = next(
        m for m in public.matches if m.kind == "placement" and m.stage_id not in encouragement
    )
    assert {bronze.side_a.entry_id, bronze.side_b.entry_id} == losers
    assert bronze.side_a.source_type == bronze.side_b.source_type == "match_loser"
