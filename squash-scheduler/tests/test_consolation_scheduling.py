"""Live consolation dependencies participate in scheduling and independent checks."""

from datetime import timedelta

from test_formats_draws import approve_draw, event
from test_scheduling import fixture

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import dependency_map, generate_matches
from squash_scheduler.domain.models import new_id
from squash_scheduler.scheduling import impacted_match_ids, solve_schedule, validate_schedule


def consolation_event():
    snapshot, division = event(8, "single_elimination")
    snapshot.config.update(fixture(courts=4, rest=15, end="16:00").config)
    division.format_plan["placement_matches"] = ["bronze"]
    approve_draw(run_draw(snapshot, division.id, 82, "tester"))
    generate_matches(snapshot)
    first_round = sorted(
        (m for m in snapshot.matches if m.kind == "competitive" and m.round == 1),
        key=lambda match: match.bracket_position,
    )
    first, second, final = (new_id() for _ in range(3))

    def definition(identifier, label, source_ids, reference_type, round_number):
        return {
            "id": identifier,
            "label": label,
            "code": label,
            "stage_key": "additional_placement",
            "stage_label": "附加名次賽",
            "side_a": {"type": reference_type, "match_id": source_ids[0]},
            "side_b": {"type": reference_type, "match_id": source_ids[1]},
            "round": round_number,
            "bracket_position": 0,
        }

    # The final is intentionally listed first: the declaration is a DAG, not a command order.
    division.format_plan["placement_matches"] = [
        "bronze",
        definition(final, "C3", (first, second), "match_winner", 2),
        definition(first, "C1", tuple(m.id for m in first_round[:2]), "match_loser", 1),
        definition(second, "C2", tuple(m.id for m in first_round[2:]), "match_loser", 1),
    ]
    approve_draw(
        run_draw(snapshot, division.id, 82, "tester", reason="Define synthetic consolation")
    )
    generate_matches(snapshot)
    return snapshot, first_round, (first, second, final)


def test_consolation_replans_follow_both_main_losers_and_later_winners():
    snapshot, main_round, (first, second, final) = consolation_event()
    dependencies = dependency_map(snapshot)
    assert dependencies[first] == {match.id for match in main_round[:2]}
    assert dependencies[final] == {first, second}
    impacted = impacted_match_ids(snapshot, {main_round[0].id})
    assert {main_round[0].id, first, final} <= impacted


def test_solver_and_independent_validator_enforce_consolation_rest_and_dependencies():
    snapshot, _, (first, _, final) = consolation_event()
    result = solve_schedule(snapshot, time_limit_seconds=10)
    assert result["status"] in {"FEASIBLE", "OPTIMAL"}, result
    assert len(snapshot.assignments) == len(snapshot.matches) == 11
    assert not any(issue.severity == "error" for issue in validate_schedule(snapshot))
    assignments = {assignment.match_id: assignment for assignment in snapshot.assignments}
    for match_id, sources in dependency_map(snapshot).items():
        for source in sources:
            assert assignments[match_id].start_at >= assignments[source].play_end_at + timedelta(
                minutes=15
            )
    broken = snapshot.model_copy(deep=True)
    target = next(assignment for assignment in broken.assignments if assignment.match_id == final)
    target.start_at = assignments[first].start_at
    target.play_end_at = target.start_at + timedelta(minutes=25)
    target.court_release_at = target.play_end_at + timedelta(minutes=5)
    assert "ADVANCEMENT_NOT_READY" in {issue.code for issue in validate_schedule(broken)}
