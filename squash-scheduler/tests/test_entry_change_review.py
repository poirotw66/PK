"""Reviewed roster changes retain approved historical topology for actual replanning."""

from copy import deepcopy

import pytest
from test_formats_draws import event
from test_rankings import result, setup
from test_scheduling import fixture
from test_withdrawals import configure_walkovers

from squash_scheduler.domain.models import Pool, PoolRankSide, new_id
from squash_scheduler.domain.rankings import (
    compute_rankings,
    import_results,
    ranking_basis,
    resolve_side,
)
from squash_scheduler.domain.validation import reviewed_entry_change, target_basis, validate
from squash_scheduler.domain.withdrawals import (
    apply_change,
    entry_change_review_issues,
    propose_change,
)
from squash_scheduler.scheduling import solve_schedule, validate_schedule


def prepared(count=4):
    from squash_scheduler.domain.matches import generate_matches

    snapshot, division, _stage = setup(count)
    snapshot.config.update(fixture(courts=2, end="15:00").config)
    configure_walkovers(snapshot)
    approval_id = new_id()
    snapshot.approvals.append(
        {
            "id": approval_id,
            "target_type": "format",
            "target_id": division.id,
            "basis_hash": target_basis(division.format_plan),
            "action": "approve",
        }
    )
    division.format_plan["approval_id"] = approval_id
    generate_matches(snapshot)
    assert solve_schedule(snapshot)["status"] in {"OPTIMAL", "FEASIBLE"}
    completed = snapshot.matches[0]
    import_results(snapshot, {"results": [result(completed)], "actor_id": "referee"})
    assert not [
        i
        for i in validate(snapshot, "G3")["issues"]
        if i["gate"] in {"G2", "G3"} and i["severity"] == "error"
    ]
    return snapshot, completed


def review(snapshot, audit):
    assert entry_change_review_issues(snapshot, audit) == []
    approval_id = new_id()
    snapshot.approvals.append(
        {
            "id": approval_id,
            "target_type": "entry_change",
            "target_id": audit["id"],
            "basis_hash": target_basis(audit),
            "action": "approve",
        }
    )
    audit.update(status="approved", approval_id=approval_id)


def test_withdrawal_requires_actual_review_then_g3_and_replan_preserve_history():
    snapshot, completed = prepared()
    history = deepcopy(snapshot.results)
    original_assignment = deepcopy(
        next(a for a in snapshot.assignments if a.match_id == completed.id)
    )
    revised, audit = apply_change(
        snapshot,
        propose_change(snapshot, completed.side_a.entry_id, actor="organizer", reason="Injury"),
    )
    assert reviewed_entry_change(revised) is None
    assert any(i["gate"] == "G3" for i in validate(revised, "G3")["issues"])
    review(revised, audit)
    assert reviewed_entry_change(revised) == audit
    assert not [
        i
        for i in validate(revised, "G3")["issues"]
        if i["gate"] in {"G2", "G3"} and i["severity"] == "error"
    ]
    assert solve_schedule(revised)["status"] in {"OPTIMAL", "FEASIBLE"}
    assert validate_schedule(revised) == []
    assert next(a for a in revised.assignments if a.match_id == completed.id) == original_assignment
    assert revised.results[: len(history)] == history


@pytest.mark.parametrize("mutation", ["roster", "side", "rule", "result", "approval", "status"])
def test_entry_review_cannot_authorize_unrelated_edits(mutation):
    snapshot, completed = prepared()
    revised, audit = apply_change(
        snapshot,
        propose_change(snapshot, completed.side_a.entry_id, actor="organizer", reason="Injury"),
    )
    review(revised, audit)
    if mutation == "roster":
        next(e for e in revised.entries if e.status == "active").status = "withdrawn"
    elif mutation == "side":
        revised.matches[-1].side_a = revised.matches[-1].side_b
    elif mutation == "rule":
        revised.config["timing_defaults"]["estimated_play_minutes"] += 1
    elif mutation == "result":
        revised.results[0]["winner_id"] = next(
            e.id for e in revised.entries if e.id != revised.results[0]["winner_id"]
        )
    elif mutation == "status":
        next(m for m in revised.matches if m.id == completed.id).status = "planned"
    else:
        revised.approvals[-1]["action"] = "reject"
    assert reviewed_entry_change(revised) is None
    assert any(
        i["gate"] in {"G2", "G3"} and i["severity"] == "error"
        for i in validate(revised, "G3")["issues"]
    )


def test_result_progression_preserves_entry_review_but_cannot_rewrite_history():
    snapshot, completed = prepared()
    revised, audit = apply_change(
        snapshot,
        propose_change(snapshot, completed.side_a.entry_id, actor="organizer", reason="Injury"),
    )
    review(revised, audit)
    pending = next(m for m in revised.matches if m.status != "completed")
    import_results(revised, {"results": [result(pending)], "actor_id": "referee"})
    assert reviewed_entry_change(revised) == audit
    assert validate_schedule(revised) == []


def test_sequential_reviewed_withdrawals_keep_a_single_linear_evidence_chain():
    snapshot, completed = prepared(5)
    for entry_id in (completed.side_a.entry_id, completed.side_b.entry_id):
        snapshot, audit = apply_change(
            snapshot, propose_change(snapshot, entry_id, actor="organizer", reason="Injury")
        )
        review(snapshot, audit)
        assert reviewed_entry_change(snapshot) == audit
        assert not [
            i
            for i in validate(snapshot, "G3")["issues"]
            if i["gate"] in {"G2", "G3"} and i["severity"] == "error"
        ]
    assert len(audit["before_snapshot"]["changes"]) == 1
    assert audit["before_snapshot"]["changes"][0]["before_snapshot"]["changes"] == []
    assert solve_schedule(snapshot)["status"] in {"OPTIMAL", "FEASIBLE"}
    assert validate_schedule(snapshot) == []


def test_reviewed_three_to_two_to_one_keeps_original_competition_count_treatment():
    snapshot, completed = prepared(3)
    for entry_id in (completed.side_a.entry_id, completed.side_b.entry_id):
        snapshot, audit = apply_change(
            snapshot, propose_change(snapshot, entry_id, actor="organizer", reason="Injury")
        )
        assert reviewed_entry_change(snapshot) is None
        review(snapshot, audit)
        report = validate(snapshot, "G3")
        assert not [i for i in report["issues"] if i["gate"] in {"G2", "G3"}]
        assert solve_schedule(snapshot)["status"] in {"OPTIMAL", "FEASIBLE"}
        assert validate_schedule(snapshot) == []
    assert sum(e.status == "active" for e in snapshot.entries) == 1


def test_reviewed_elimination_withdrawal_preserves_original_approved_draw():
    from squash_scheduler.domain.draws import run_draw
    from squash_scheduler.domain.matches import generate_matches

    rules_source, _, _ = setup(4)
    snapshot, division = event(4, template="single_elimination")
    snapshot.config.update(fixture(courts=2, end="15:00").config)
    snapshot.config["match_rule_defaults"] = rules_source.config["match_rule_defaults"]
    configure_walkovers(snapshot)
    for kind, target, value in [("format", division.id, division.format_plan)]:
        approval_id = new_id()
        snapshot.approvals.append(
            {
                "id": approval_id,
                "target_type": kind,
                "target_id": target,
                "basis_hash": target_basis(value),
                "action": "approve",
            }
        )
        value.update(status="approved", approval_id=approval_id)
    draw = run_draw(snapshot, division.id, 7, "organizer")
    approval_id = new_id()
    snapshot.approvals.append(
        {
            "id": approval_id,
            "target_type": "draw",
            "target_id": draw["id"],
            "basis_hash": target_basis(draw),
            "action": "approve",
        }
    )
    draw.update(status="approved", approval_id=approval_id)
    generate_matches(snapshot)
    assert solve_schedule(snapshot)["status"] in {"OPTIMAL", "FEASIBLE"}
    original_draw = deepcopy(draw)
    entry_id = snapshot.matches[0].side_a.entry_id
    revised, audit = apply_change(
        snapshot, propose_change(snapshot, entry_id, actor="organizer", reason="Injury")
    )
    review(revised, audit)
    assert revised.draws[0] == original_draw
    assert not [
        i
        for i in validate(revised, "G3")["issues"]
        if i["gate"] in {"G2", "G3"} and i["severity"] == "error"
    ]
    assert solve_schedule(revised)["status"] in {"OPTIMAL", "FEASIBLE"}
    assert validate_schedule(revised) == []


@pytest.mark.parametrize("manual", [False, True])
def test_pool_advancement_filters_withdrawn_leader_without_rewriting_rankings(manual):
    from squash_scheduler.domain.revisions import approve

    snapshot, _division, stage = setup(3)
    ordered = sorted(e.id for e in snapshot.entries)
    pool = Pool(stage_id=stage.id, label="A", entry_ids=ordered, advancement_count=2)
    snapshot.pools.append(pool)
    batch = []
    for match in snapshot.matches:
        winner_a = not (
            manual and match.side_a.entry_id == ordered[0] and match.side_b.entry_id == ordered[2]
        )
        batch.append(result(match, winner_a=winner_a))
    import_results(snapshot, {"results": batch})
    if manual:
        assert resolve_side(snapshot, PoolRankSide(pool_id=pool.id, rank=1)) is None
        decision = {
            "id": new_id(),
            "stage_id": stage.id,
            "pool_id": pool.id,
            "ordered_entry_ids": ordered,
            "basis_hash": ranking_basis(snapshot, stage.id),
            "reason": "Organizer resolved the tied standings",
            "status": "draft",
            "approval_id": None,
        }
        snapshot.ranking_decisions.append(decision)
        snapshot, _ = approve(
            snapshot,
            f"ranking:{decision['id']}",
            target_basis(decision),
            "reviewer",
            decision["reason"],
        )
    historical = deepcopy(compute_rankings(snapshot, stage.id)["standings"])
    assert historical[0]["entry_id"] == ordered[0]
    next(e for e in snapshot.entries if e.id == ordered[0]).status = "withdrawn"
    assert compute_rankings(snapshot, stage.id)["standings"] == historical
    assert resolve_side(snapshot, PoolRankSide(pool_id=pool.id, rank=1)) == ordered[1]
    assert resolve_side(snapshot, PoolRankSide(pool_id=pool.id, rank=2)) == ordered[2]
    if manual:
        snapshot.approvals[-1]["status"] = "stale"
        assert resolve_side(snapshot, PoolRankSide(pool_id=pool.id, rank=1)) is None
