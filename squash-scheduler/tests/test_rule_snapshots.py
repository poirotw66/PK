"""Generation fixes inherited rules, preserving already played conditions across amendments."""

from copy import deepcopy

from test_rankings import result, setup
from test_review_decisions import patch_for, reviewed_event

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.rankings import import_results
from squash_scheduler.domain.revisions import apply_patch
from squash_scheduler.domain.rules import refresh_rule_snapshots
from squash_scheduler.domain.validation import validate
from squash_scheduler.exporters.documents import view_model
from squash_scheduler.scheduling.constraints import effective_timing


def test_regeneration_preserves_stage_override_and_expands_it_into_matches():
    snapshot, _, stage = setup()
    stage.rule_set_id = "synthetic-stage-rules"
    snapshot.config["rule_sets"] = {
        stage.rule_set_id: {
            "match_rules": {"best_of_games": 1},
            "timing": {"estimated_play_minutes": 37},
        }
    }
    generate_matches(snapshot)
    assert snapshot.stages[0].rule_set_id == "synthetic-stage-rules"
    for match in snapshot.matches:
        assert match.rule_snapshot["match_rules"]["best_of_games"] == 1
        assert effective_timing(snapshot, match)["estimated_play_minutes"] == 37


def test_first_result_uses_generated_rules_even_if_live_defaults_are_edited():
    snapshot, _, _ = setup()
    match = snapshot.matches[0]
    snapshot.config["match_rule_defaults"].update(best_of_games=5, points_to_win_game=15)
    import_results(snapshot, {"actor_id": "synthetic", "results": [result(match)]})
    assert snapshot.results[0]["rule_snapshot"]["best_of_games"] == 3
    assert snapshot.results[0]["rule_snapshot"]["points_to_win_game"] == 11
    model = view_model(snapshot)
    assert model["rule_summaries"][0]["lines"][0] == "3 局 2 勝"


def test_explicit_refresh_keeps_started_rules_and_updates_unstarted_matches():
    snapshot, _, _ = setup()
    snapshot.config["timing_defaults"] = {"estimated_play_minutes": 25}
    refresh_rule_snapshots(snapshot)
    started = snapshot.matches[0]
    started.status = "in_progress"
    frozen = deepcopy(started.rule_snapshot)
    snapshot.config["timing_defaults"]["estimated_play_minutes"] = 55
    snapshot.config["match_rule_defaults"]["best_of_games"] = 5
    generate_matches(snapshot)
    current = next(m for m in snapshot.matches if m.id == started.id)
    assert current.rule_snapshot == frozen
    assert effective_timing(snapshot, current)["estimated_play_minutes"] == 25
    assert all(
        m.rule_snapshot["timing"]["estimated_play_minutes"] == 55
        for m in snapshot.matches
        if m.id != started.id
    )


def test_configuration_patch_refreshes_new_draft_without_modifying_previous_match_rules():
    snapshot, _ = reviewed_event(3)
    generate_matches(snapshot)
    original = deepcopy(snapshot.model_dump(mode="json"))
    changed = {**snapshot.config["match_rule_defaults"], "best_of_games": 5}
    updated, _ = apply_patch(
        snapshot,
        patch_for(
            snapshot, [{"op": "set_config", "field": "match_rule_defaults", "value": changed}]
        ),
    )
    assert snapshot.model_dump(mode="json") == original
    assert all(m.rule_snapshot["match_rules"]["best_of_games"] == 5 for m in updated.matches)


def test_g5_checks_complete_inherited_rules_instead_of_requiring_duplicate_defaults():
    snapshot, division = reviewed_event(3)
    snapshot.config["match_rule_defaults"].update(
        best_of_games=None,
        points_to_win_game=None,
        win_by_points=None,
        point_cap_mode="unconfigured",
    )
    division.rule_overrides = {
        "match_rules": {
            "best_of_games": 3,
            "points_to_win_game": 11,
            "win_by_points": 2,
            "point_cap_mode": "none",
            "late_arrival_rule": "人工處理",
            "walkover_rule": "人工處理",
            "retirement_rule": "人工處理",
            "double_forfeit_rule": "人工處理",
            "replacement_rule": "人工處理",
            "ranking_denominator_rule": "以人工核定為準",
        }
    }
    generate_matches(snapshot)
    report = validate(snapshot, "G5")
    assert not any(
        i["code"].startswith("RULES_CONFIG") or i["code"] == "RULE_SNAPSHOT_MISSING"
        for i in report["issues"]
    )
