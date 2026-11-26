"""Group-by-group creation, reviewed coach rules, and protected manual swaps."""

import pytest
from test_formats_draws import event
from test_operator import confirm, operator_snapshot, preview

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.encouragement import eligibility_report
from squash_scheduler.domain.match_rules import effective_match_rules, is_championship
from squash_scheduler.domain.matches import elimination
from squash_scheduler.domain.models import Stage, stable_id
from squash_scheduler.operator_creation import add_division, apply_coach_rules, edit_draw


@pytest.fixture(name="service")
def creation_service(tmp_path):
    from squash_scheduler.operations import OperatorService
    from squash_scheduler.storage.repository import Repository

    snapshot, _ = operator_snapshot()
    repo = Repository(tmp_path / "event")
    with repo.lock():
        repo.save(snapshot, None)
    operator = OperatorService(repo)
    yield operator
    operator.close()


def test_group_roster_preview_uses_canonical_entries(service):
    initial = service.repo.load()
    plan = preview(
        service,
        action="division_roster",
        roster={
            "name": "新組",
            "names": ["甲", "乙", "丙", "丁"],
            "category": "competitive",
            "gender_category": "mixed",
            "reuse_existing": False,
        },
    )
    assert len(service.repo.load().entries) == len(initial.entries)
    confirm(service, plan)
    saved = service.repo.load()
    group = next(d for d in saved.divisions if d.name == "新組")
    assert len([e for e in saved.entries if e.effective_division_id == group.id]) == 4
    assert group.format_plan is None
    assert saved.config["registration"]["division_aliases"]["新組"] == group.id


def test_coach_rules_match_specific_and_preserve_played():
    snapshot, division = event(4, "single_elimination")
    snapshot.config["match_rule_defaults"] = {"best_of_games": 3, "points_to_win": 11}
    stage = Stage(id=stable_id("test-stage"), division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    snapshot.matches = elimination(
        [e.id for e in snapshot.entries], division.id, stage.id, bracket_capacity=4
    )
    final = next(m for m in snapshot.matches if is_championship(snapshot, m))
    first = next(m for m in snapshot.matches if m.round == 1)
    first.status = "completed"
    first.rule_snapshot = {"match_rules": {"best_of_games": 3, "points_to_win": 11}, "timing": {}}
    apply_coach_rules(snapshot)
    assert effective_match_rules(snapshot, final)["best_of_games"] == 5
    assert effective_match_rules(snapshot, first)["best_of_games"] == 3
    assert first.rule_snapshot["match_rules"]["best_of_games"] == 3


def test_new_encouragement_policy_includes_seed_but_not_pool_knockout():
    snapshot, division = event(4, "single_elimination")
    snapshot.config["runtime"] = {"coach_competition_rules": True}
    snapshot.config["draw_defaults"]["seeded_entries_mode"] = "provided"
    snapshot.config["draw_defaults"]["seeded_entries"] = [{"entry_id": snapshot.entries[0].id}]
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    snapshot.matches = elimination(
        [e.id for e in snapshot.entries], division.id, stage.id, bracket_capacity=4
    )
    for match in snapshot.matches:
        if match.round == 1:
            match.status = "completed"
            snapshot.results.append(
                {
                    "match_id": match.id,
                    "status": "confirmed",
                    "outcome": "played",
                    "winner_id": match.side_b.entry_id,
                }
            )
    assert snapshot.entries[0].id in eligibility_report(snapshot, division.id)["eligible_entry_ids"]
    division.format_plan["template_id"] = "pools_knockout"
    assert not eligibility_report(snapshot, division.id)["eligible_entry_ids"]


def test_swap_draw_retains_constraints_and_requires_review():
    snapshot, division = event(4, "single_elimination")
    record = run_draw(snapshot, division.id, 9, "test", "first")
    slots = [s["entry_id"] for s in record["output"]["slots"]]
    swapped = slots[::-1]
    summary = edit_draw(snapshot, record["id"], swapped, "test", "reviewed swap")
    assert summary["output"]["bracket_entry_ids"] == swapped
    assert snapshot.draws[-1]["status"] == "review_required"
    assert record["status"] == "superseded"
    assert snapshot.draws[-1]["input_hash"] == record["input_hash"]
    with pytest.raises(ValueError):
        edit_draw(snapshot, snapshot.draws[-1]["id"], [slots[0]] * 4, "test", "bad")


def test_reviewed_rules_and_manual_swap_confirm_to_real_graph(service):
    from test_operator_formats import choice

    confirm(service, preview(service, action="coach_rules"))
    assert service.state()["coach_competition_rules"] is True
    confirm(
        service,
        preview(
            service,
            action="division_roster",
            roster={
                "name": "四人組",
                "names": ["一", "二", "三", "四"],
                "category": "competitive",
                "gender_category": "male",
                "reuse_existing": False,
            },
        ),
    )
    division = next(d for d in service.repo.load().divisions if d.name == "四人組")
    confirm(
        service,
        preview(
            service,
            action="format",
            division_id=division.id,
            format=choice("single_elimination", bronze=False, encouragement=True),
        ),
    )
    confirm(
        service, preview(service, action="seeds", division_id=division.id, mode="none", seeds=[])
    )
    confirm(service, preview(service, action="draw", division_id=division.id, seed=21))
    draw = next(d for d in reversed(service.repo.load().draws) if d["division_id"] == division.id)
    positions = [s["entry_id"] for s in draw["output"]["slots"]][::-1]
    confirm(
        service, preview(service, action="draw_positions", draw_id=draw["id"], positions=positions)
    )
    draw = next(d for d in reversed(service.repo.load().draws) if d["division_id"] == division.id)
    confirm(service, preview(service, action="approve_draw", draw_id=draw["id"]))
    saved = service.repo.load()
    matches = [m for m in saved.matches if m.division_id == division.id]
    assert len(matches) == 3
    final = next(m for m in matches if is_championship(saved, m))
    assert final.rule_snapshot["match_rules"]["best_of_games"] == 5
    assert {m.rule_snapshot["match_rules"]["best_of_games"] for m in matches if m.round == 1} == {3}
    assert division.id in {r["division_id"] for r in service.state()["eligibility"]}
    for match in matches:
        if match.round == 1:
            confirm(
                service,
                preview(
                    service,
                    action="results",
                    results=[
                        {
                            "match_id": match.id,
                            "status": "confirmed",
                            "outcome": "played",
                            "winner_id": match.side_a.entry_id,
                            "games": [{"side_a": 11, "side_b": 4}, {"side_a": 11, "side_b": 5}],
                        }
                    ],
                ),
            )
    state_final = next(m for m in service.state()["matches"] if m["id"] == final.id)
    scores = [{"side_a": 11, "side_b": 4}, {"side_a": 11, "side_b": 5}]
    final_result = {
        "match_id": final.id,
        "status": "confirmed",
        "outcome": "played",
        "winner_id": state_final["entry_a"],
        "games": scores,
    }
    with pytest.raises(ValueError):
        preview(service, action="results", results=[final_result])
    final_result["games"] = scores + [{"side_a": 11, "side_b": 6}]
    confirm(service, preview(service, action="results", results=[final_result]))
    assert (
        next(m for m in service.state()["matches"] if m["id"] == final.id)["status"] == "completed"
    )


def test_cross_division_identity_reuse_requires_explicit_confirmation():
    snapshot, _division = event(4)
    player = snapshot.participants[0]
    roster = {
        "name": "跨組",
        "names": [player.official_name, "其他人"],
        "category": "competitive",
        "gender_category": "mixed",
        "reuse_existing": False,
    }
    with pytest.raises(ValueError, match="沿用同名選手"):
        add_division(snapshot, roster)
    assert len(snapshot.divisions) == 1
    roster["reuse_existing"] = True
    summary = add_division(snapshot, roster)
    entries = [e for e in snapshot.entries if e.effective_division_id == summary["division_id"]]
    registrations = {r.id: r for r in snapshot.registrations}
    assert player.id in {registrations[e.registration_id].participant_id for e in entries}
    assert len(snapshot.participants) == 5
