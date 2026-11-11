"""Prefer one division per slot while retaining availability and lock constraints."""

from test_scheduling import assign, at, fixture, successful

from squash_scheduler.scheduling import solve_schedule, validate_schedule


def batch_fixture():
    s = fixture(courts=2)
    s.config["timing_defaults"]["estimated_play_minutes"] = 10
    s.config["runtime"]["schedule_objective"] = "round_robin_batches"
    s.config["runtime"]["batch_slot_minutes"] = 15
    for stage in s.stages:
        stage.type = "round_robin"
    return s


def test_batch_mode_separates_divisions_when_slots_are_available():
    s = batch_fixture()
    r = solve_schedule(s, time_limit_seconds=5)
    assert successful(r), r
    assert not validate_schedule(s)
    assert len({a.start_at for a in s.assignments}) == 2
    assert all(a.start_at.minute % 15 == 0 for a in s.assignments)
    assert r["diagnostics"]["objective"]["strategy"] == "round_robin_batches"
    assert "minimize_mixed_division_slots" in r["diagnostics"]["objective"]["priority"]


def test_batch_preference_does_not_override_locked_mixed_slot():
    s = batch_fixture()
    s.assignments = [
        assign(s, 0, "08:00", play=10, locks={"time": True, "court": True}),
        assign(s, 1, "08:00", court=1, play=10, locks={"time": True, "court": True}),
    ]
    before = [a.model_copy(deep=True) for a in s.assignments]
    assert successful(solve_schedule(s, time_limit_seconds=5))
    assert s.assignments == before
    assert not validate_schedule(s)


def test_preferred_windows_allow_fallback_without_reducing_player_rest():
    s = fixture(shared=True, courts=2)
    s.config["player_workload"] = {"max_matches_per_day": 5, "minimum_rest_minutes": 60}
    s.config["runtime"]["preferred_schedule_windows"] = [
        {
            "division_id": division.id,
            "stage_type": "elimination",
            "start_at": at("08:00").isoformat(),
            "end_at": at("08:45").isoformat(),
        }
        for division in s.divisions
    ]
    result = solve_schedule(s, time_limit_seconds=5)
    assert successful(result), result
    report = result["diagnostics"]["preferred_windows"]
    first, second = sorted(s.assignments, key=lambda assignment: assignment.start_at)
    assert first.start_at < at("08:45") <= second.start_at
    assert (second.start_at - first.play_end_at).total_seconds() >= 3600
    assert report == {
        "requested_count": 2,
        "in_window_count": 1,
        "outside_match_ids": [second.match_id],
    }
    assert not validate_schedule(s)


def test_preferred_windows_reject_unknown_dates_in_initial_solve():
    s = batch_fixture()
    s.config["runtime"]["preferred_schedule_windows"] = [
        {
            "division_id": s.divisions[0].id,
            "stage_type": "round_robin",
            "start_at": at("08:00", "2026-11-02").isoformat(),
            "end_at": at("09:00", "2026-11-02").isoformat(),
        }
    ]
    result = solve_schedule(s, time_limit_seconds=5)
    assert result["status"] == "MODEL_INVALID"
    assert "PREFERRED_SCHEDULE_WINDOW_INVALID" in {issue["code"] for issue in result["issues"]}
    assert not s.assignments


def test_same_day_policy_is_enforced_by_solver_and_independent_validator():
    from datetime import timedelta

    from test_player_workload import two_days

    s = batch_fixture()
    two_days(s)
    from squash_scheduler.domain.matches import round_robin

    s.entries = s.entries[:3]
    for entry in s.entries:
        entry.effective_division_id = s.divisions[0].id
    s.matches = round_robin([entry.id for entry in s.entries], s.divisions[0].id, s.stages[0].id)
    s.assignments = [
        assign(s, 0, "08:00", play=10),
        assign(s, 1, "10:00", play=10),
        assign(s, 2, "11:00", play=10),
    ]
    for index in [1, 2]:
        s.assignments[index] = s.assignments[index].model_copy(
            update={
                key: getattr(s.assignments[index], key) + timedelta(days=1)
                for key in ["start_at", "play_end_at", "court_release_at"]
            }
        )
    assert not validate_schedule(s)
    s.config["runtime"]["keep_round_robin_groups_same_day"] = True
    assert "ROUND_ROBIN_GROUP_SPLIT" in {i.code for i in validate_schedule(s)}
    assert successful(solve_schedule(s, time_limit_seconds=5))
    assert not validate_schedule(s)
    assert len({a.start_at.date() for a in s.assignments}) == 1
