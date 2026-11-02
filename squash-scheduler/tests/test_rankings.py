"""Synthetic results/ranking acceptance, with no historical outcome assumptions."""

import pytest
from test_formats_draws import event

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import elimination, generate_matches
from squash_scheduler.domain.models import (
    DrawSlotSide,
    EntrySide,
    Match,
    PoolRankSide,
    Stage,
    WinnerSide,
    new_id,
)
from squash_scheduler.domain.rankings import (
    compute_rankings,
    effective_rules,
    import_results,
    ranking_basis,
    resolve_side,
)
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.rules import refresh_rule_snapshots
from squash_scheduler.domain.validation import target_basis


def setup(count=3):
    snapshot, division = event(count)
    snapshot.config["match_rule_defaults"] = {
        "best_of_games": 3,
        "points_to_win_game": 11,
        "win_by_points": 2,
        "point_cap_mode": "none",
        "point_cap": None,
    }
    generate_matches(snapshot)
    return snapshot, division, snapshot.stages[0]


def result(match, winner_a=True):
    winner = match.side_a.entry_id if winner_a else match.side_b.entry_id
    score = {"side_a": 11, "side_b": 4} if winner_a else {"side_a": 4, "side_b": 11}
    return {
        "match_id": match.id,
        "outcome": "played",
        "status": "confirmed",
        "winner_id": winner,
        "games": [score.copy(), score.copy()],
    }


def test_atomic_validated_batch_and_frozen_expanded_rules():
    snapshot, division, stage = setup()
    division.rule_overrides = {"match_rules": {"best_of_games": 1}}
    stage.rule_set_id = "synthetic-stage"
    snapshot.config["rule_sets"] = {"synthetic-stage": {"match_rules": {"points_to_win_game": 5}}}
    refresh_rule_snapshots(snapshot)
    assert effective_rules(snapshot, stage.id)["best_of_games"] == 1
    assert effective_rules(snapshot, stage.id)["points_to_win_game"] == 5
    first, second = snapshot.matches[:2]
    valid = result(first)
    valid["games"] = [{"side_a": 5, "side_b": 2}]
    invalid = result(second)
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError):
        import_results(snapshot, {"results": [valid, invalid]})
    assert snapshot.model_dump(mode="json") == before
    import_results(snapshot, {"results": [valid], "actor_id": "tester"})
    assert snapshot.results[0]["rule_snapshot"]["points_to_win_game"] == 5
    snapshot.config["match_rule_defaults"]["best_of_games"] = 5
    assert snapshot.results[0]["rule_snapshot"]["best_of_games"] == 1


@pytest.mark.parametrize(
    "games",
    [
        [{"side_a": 11, "side_b": 10}] * 2,
        [{"side_a": 12, "side_b": 4}] * 2,
        [{"side_a": 11, "side_b": 4}] * 3,
        [{"side_a": -1, "side_b": 4}] * 2,
        [{"side_a": True, "side_b": 4}] * 2,
    ],
)
def test_illegal_game_or_post_victory_score_is_rejected(games):
    snapshot, _, _ = setup()
    raw = result(snapshot.matches[0])
    raw["games"] = games
    with pytest.raises(ValueError):
        import_results(snapshot, {"results": [raw]})
    assert not snapshot.results


def test_declared_winner_and_point_cap_are_verified():
    snapshot, _, _ = setup()
    raw = result(snapshot.matches[0])
    raw["winner_id"] = snapshot.matches[0].side_b.entry_id
    with pytest.raises(ValueError, match="declared winner"):
        import_results(snapshot, {"results": [raw]})
    snapshot.config["match_rule_defaults"].update(point_cap_mode="enabled", point_cap=15)
    refresh_rule_snapshots(snapshot)
    raw = result(snapshot.matches[0])
    raw["games"] = [{"side_a": 15, "side_b": 14}] * 2
    import_results(snapshot, {"results": [raw]})
    assert snapshot.matches[0].status == "completed"


def test_full_round_robin_stats_two_person_tie_and_empty_denominator():
    snapshot, _, stage = setup()
    provisional = compute_rankings(snapshot, stage.id)
    assert provisional["status"] == "provisional"
    assert all(
        r["average_points"] is None and r["win_rate"] is None for r in provisional["standings"]
    )
    # Entry ID order defines A/B/C, independent of names.
    ordered = sorted(e.id for e in snapshot.entries)
    batch = [result(m, winner_a=True) for m in snapshot.matches]
    import_results(snapshot, {"results": batch})
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "final"
    assert [r["entry_id"] for r in report["standings"]] == ordered
    winner = report["standings"][0]
    assert (
        winner["points"],
        winner["played_count"],
        winner["adjudicated_count"],
        winner["average_points"],
        winner["win_rate"],
    ) == (4, 2, 2, 2, 1)
    assert winner["game_difference"] == 4 and winner["point_difference"] == 28


def test_three_way_tie_requires_manual_content_bound_ranking_and_no_name_fallback():
    snapshot, _, stage = setup()
    entries = sorted(e.id for e in snapshot.entries)
    wins = {entries[0]: entries[1], entries[1]: entries[2], entries[2]: entries[0]}
    batch = []
    for match in snapshot.matches:
        a, b = match.side_a.entry_id, match.side_b.entry_id
        batch.append(result(match, wins[a] == b))
    import_results(snapshot, {"results": batch})
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "manual_review"
    assert len(report["groups"][0]["tie_groups"][0]) == 3
    assert all(r["rank"] is None for r in report["standings"])
    decision = {
        "id": new_id(),
        "stage_id": stage.id,
        "ordered_entry_ids": list(reversed(entries)),
        "basis_hash": ranking_basis(snapshot, stage.id),
        "reason": "Synthetic organizer review",
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
    assert compute_rankings(snapshot, stage.id)["status"] == "final"
    correction = result(snapshot.matches[0], winner_a=False)
    import_results(snapshot, {"results": [correction], "reason": "Correct synthetic score"})
    assert any(
        i["code"] == "APPROVAL_STALE" for i in compute_rankings(snapshot, stage.id)["issues"]
    )
    assert sum(r["status"] == "superseded" for r in snapshot.results) == 1


def test_incomplete_round_robin_manual_review_and_exhibitions_never_count():
    snapshot, _division, stage = setup()
    excluded = snapshot.matches[0]
    excluded.status = "excluded"
    snapshot.exclusions.append(
        {
            "id": new_id(),
            "match_id": excluded.id,
            "approval_id": new_id(),
            "reason": "Synthetic exclusion",
        }
    )
    exhibition = snapshot.matches[1].model_copy(
        deep=True, update={"id": new_id(), "kind": "exhibition"}
    )
    snapshot.matches.append(exhibition)
    import_results(
        snapshot, {"results": [result(m) for m in snapshot.matches if m.status != "excluded"]}
    )
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "manual_review"
    assert sum(r["played_count"] for r in report["standings"]) == 4
    decision = {
        "id": new_id(),
        "stage_id": stage.id,
        "ordered_entry_ids": [e.id for e in snapshot.entries],
        "basis_hash": ranking_basis(snapshot, stage.id),
        "reason": "Unequal schedule reviewed",
        "approval_id": None,
        "status": "draft",
    }
    snapshot.ranking_decisions.append(decision)
    snapshot, _ = approve(
        snapshot,
        f"ranking:{decision['id']}",
        target_basis(decision),
        "reviewer",
        decision["reason"],
    )
    assert compute_rankings(snapshot, stage.id)["status"] == "final"
    snapshot.exclusions[0]["reason"] = "Changed exclusion evidence"
    assert compute_rankings(snapshot, stage.id)["status"] == "manual_review"


def test_walkover_requires_explicit_denominator_and_has_no_invented_games():
    snapshot, _, stage = setup(2)
    match = snapshot.matches[0]
    raw = {
        "match_id": match.id,
        "outcome": "walkover",
        "status": "confirmed",
        "winner_id": match.side_a.entry_id,
        "games": [],
    }
    with pytest.raises(ValueError, match="walkover_rule"):
        import_results(snapshot, {"results": [raw]})
    rules = snapshot.config["match_rule_defaults"]
    rules["walkover_rule"] = {
        "include_in_rankings": True,
        "counts_as_played": False,
        "winner_points": 2,
        "loser_points": 0,
    }
    refresh_rule_snapshots(snapshot)
    with pytest.raises(ValueError, match="ranking_denominator_rule"):
        import_results(snapshot, {"results": [raw]})
    rules["ranking_denominator_rule"] = {"walkover": True}
    refresh_rule_snapshots(snapshot)
    raw["games"] = [{"side_a": 11, "side_b": 0}] * 2
    with pytest.raises(ValueError, match="synthetic"):
        import_results(snapshot, {"results": [raw]})
    raw["games"] = []
    import_results(snapshot, {"results": [raw]})
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "final"
    assert sum(r["played_count"] for r in report["standings"]) == 0
    assert sum(r["games_for"] for r in report["standings"]) == 0
    assert report["standings"][0]["adjudicated_count"] == 1
    assert report["standings"][1]["forfeits"] == 1


def test_started_downstream_blocks_correction_and_preserves_previous_result():
    snapshot, division, _ = setup(4)
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    knockout = elimination([e.id for e in snapshot.entries], division.id, stage.id)
    snapshot.matches.extend(knockout)
    first, second, final = knockout
    import_results(snapshot, {"results": [result(first), result(second)]})
    assert resolve_side(snapshot, WinnerSide(match_id=first.id)) == first.side_a.entry_id
    final.status = "in_progress"
    # import_results atomically replaces match objects; update the current snapshot instance.
    next(m for m in snapshot.matches if m.id == final.id).status = "in_progress"
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError, match="started downstream"):
        import_results(snapshot, {"results": [result(first, False)], "reason": "Correction"})
    assert snapshot.model_dump(mode="json") == before


def test_separate_pool_rankings_resolve_only_after_finalization():
    from squash_scheduler.domain.models import Pool

    snapshot, division, stage = setup(4)
    entries = [e.id for e in snapshot.entries]
    pools = [
        Pool(stage_id=stage.id, label="A", entry_ids=entries[:2], advancement_count=1),
        Pool(stage_id=stage.id, label="B", entry_ids=entries[2:], advancement_count=1),
    ]
    snapshot.pools = pools
    snapshot.matches = [
        Match(
            division_id=division.id,
            stage_id=stage.id,
            side_a=EntrySide(entry_id=p.entry_ids[0]),
            side_b=EntrySide(entry_id=p.entry_ids[1]),
        )
        for p in pools
    ]
    side = PoolRankSide(pool_id=pools[0].id, rank=1)
    assert resolve_side(snapshot, side) is None
    import_results(snapshot, {"results": [result(snapshot.matches[0])]})
    report = compute_rankings(snapshot, stage.id)
    assert [g["status"] for g in report["groups"]] == ["final", "provisional"]
    assert resolve_side(snapshot, side) == pools[0].entry_ids[0]


def test_retirement_preserves_partial_actual_scores_and_never_invents_games():
    snapshot, _, stage = setup(2)
    rules = snapshot.config["match_rule_defaults"]
    rules["retirement_rule"] = {
        "include_in_rankings": True,
        "counts_as_played": True,
        "winner_points": 2,
        "loser_points": 0,
    }
    rules["ranking_denominator_rule"] = {"retired": True}
    refresh_rule_snapshots(snapshot)
    match = snapshot.matches[0]
    raw = {
        "match_id": match.id,
        "outcome": "retired",
        "status": "confirmed",
        "winner_id": match.side_b.entry_id,
        "games": [{"side_a": 11, "side_b": 7}, {"side_a": 3, "side_b": 5}],
    }
    import_results(snapshot, {"results": [raw]})
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "final"
    winner = report["standings"][0]
    assert winner["entry_id"] == match.side_b.entry_id
    assert winner["played_count"] == winner["adjudicated_count"] == 1
    assert (
        winner["games_for"],
        winner["games_against"],
        winner["points_for"],
        winner["points_against"],
    ) == (0, 1, 12, 14)
    raw["games"][-1] = {"side_a": 20, "side_b": 5}
    with pytest.raises(ValueError, match="Retirement score exceeds"):
        import_results(snapshot, {"results": [raw], "reason": "Invalid correction"})


def test_double_forfeit_can_explicitly_exclude_denominator_and_keeps_null_averages():
    snapshot, _, stage = setup(2)
    snapshot.config["match_rule_defaults"].update(
        double_forfeit_rule={
            "include_in_rankings": True,
            "counts_as_played": False,
            "side_a_points": 0,
            "side_b_points": 0,
        },
        ranking_denominator_rule={"double_forfeit": False},
    )
    refresh_rule_snapshots(snapshot)
    match = snapshot.matches[0]
    import_results(
        snapshot,
        {
            "results": [
                {
                    "match_id": match.id,
                    "outcome": "double_forfeit",
                    "games": [],
                    "winner_id": None,
                    "status": "confirmed",
                }
            ]
        },
    )
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "manual_review"
    assert all(
        r["forfeits"] == 1 and r["average_points"] is None and r["win_rate"] is None
        for r in report["standings"]
    )


def test_four_entry_two_way_points_tie_uses_head_to_head_before_game_difference():
    snapshot, _, stage = setup(4)
    entries = sorted(e.id for e in snapshot.entries)
    # A beats B/C, B beats C/D, C beats D. A and B both have two wins; A won their match.
    winners = {
        (entries[0], entries[1]): entries[0],
        (entries[0], entries[2]): entries[0],
        (entries[0], entries[3]): entries[3],
        (entries[1], entries[2]): entries[1],
        (entries[1], entries[3]): entries[1],
        (entries[2], entries[3]): entries[2],
    }
    batch = [
        result(m, winners[(m.side_a.entry_id, m.side_b.entry_id)] == m.side_a.entry_id)
        for m in snapshot.matches
    ]
    import_results(snapshot, {"results": batch})
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "final"
    assert [r["entry_id"] for r in report["standings"]][:2] == entries[:2]


@pytest.mark.parametrize(
    "tamper",
    ["missing_action", "forged_id", "wrong_target", "wrong_hash", "stale_action", "changed_order"],
)
def test_manual_ranking_rejects_forged_or_stale_approval(tamper):
    snapshot, _, stage = setup()
    snapshot.matches[0].status = "excluded"
    import_results(snapshot, {"results": [result(m) for m in snapshot.matches[1:]]})
    decision = {
        "id": new_id(),
        "stage_id": stage.id,
        "ordered_entry_ids": [e.id for e in snapshot.entries],
        "basis_hash": ranking_basis(snapshot, stage.id),
        "reason": "Synthetic incomplete schedule reviewed",
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
    assert compute_rankings(snapshot, stage.id)["status"] == "final"
    current = snapshot.ranking_decisions[-1]
    action = snapshot.approvals[-1]
    if tamper == "missing_action":
        snapshot.approvals.clear()
    elif tamper == "forged_id":
        current["approval_id"] = new_id()
    elif tamper == "wrong_target":
        action["target_id"] = new_id()
    elif tamper == "wrong_hash":
        action["basis_hash"] = "forged-basis"
    elif tamper == "stale_action":
        action["status"] = "stale"
    else:
        current["ordered_entry_ids"].reverse()
    report = compute_rankings(snapshot, stage.id)
    assert report["status"] == "manual_review"
    assert "APPROVAL_STALE" in {i["code"] for i in report["issues"]}
    assert all(row["rank"] is None for row in report["standings"])


@pytest.mark.parametrize(
    "tamper", ["missing_action", "wrong_action", "stale_action", "changed_inputs", "changed_output"]
)
def test_draw_slot_requires_authentic_approval_and_current_draw_inputs(tamper):
    snapshot, division, _ = setup()
    record = run_draw(snapshot, division.id, 41, "organizer")
    side = DrawSlotSide(draw_run_id=record["id"], slot_id="1")
    # A forged approved flag and UUID provide no authority to resolve a slot.
    record["status"], record["approval_id"] = "approved", new_id()
    assert resolve_side(snapshot, side) is None
    record["status"], record["approval_id"] = "review_required", None
    snapshot, _ = approve(
        snapshot,
        f"draw:{record['id']}",
        target_basis(record),
        "reviewer",
        "Reviewed synthetic draw",
    )
    expected = snapshot.draws[-1]["output"]["slots"][0]["entry_id"]
    assert resolve_side(snapshot, side) == expected
    if tamper == "missing_action":
        snapshot.approvals.clear()
    elif tamper == "wrong_action":
        snapshot.approvals[-1]["action"] = "review"
    elif tamper == "stale_action":
        snapshot.approvals[-1]["status"] = "stale"
    elif tamper == "changed_inputs":
        snapshot.divisions[0].draw_overrides["fixed_positions"] = {snapshot.entries[0].id: "1"}
    else:
        snapshot.draws[-1]["output"]["slots"][0]["entry_id"] = new_id()
    assert resolve_side(snapshot, side) is None
