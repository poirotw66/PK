"""Compact mode reduces avoidable group waiting without changing hard constraints."""

import pytest
from test_scheduling import assign, fixture, successful

from squash_scheduler.domain.models import EntrySide, Pool, new_id
from squash_scheduler.scheduling import solve_schedule, validate_schedule


def test_compact_group_moves_spread_matches_together_after_rest():
    s = fixture(shared=True, end="18:00")
    first = s.matches[0]
    # Three entries play a complete round robin in one stage.
    s.entries[2].effective_division_id = first.division_id
    s.matches = [
        first,
        first.model_copy(
            update={
                "id": new_id(),
                "side_a": EntrySide(entry_id=s.entries[0].id),
                "side_b": EntrySide(entry_id=s.entries[2].id),
            }
        ),
    ]
    # Use distinct people; cross-division registration fixtures deliberately share identities.
    s.registrations[2].participant_id = s.participants[2].id
    s.matches.append(
        first.model_copy(
            update={
                "id": new_id(),
                "side_a": EntrySide(entry_id=s.entries[1].id),
                "side_b": EntrySide(entry_id=s.entries[2].id),
            }
        )
    )
    s.stages[0].type = "round_robin"
    s.pools = [
        Pool(
            stage_id=first.stage_id,
            label="A",
            entry_ids=[s.entries[i].id for i in [0, 1, 2]],
            advancement_count=2,
        )
    ]
    s.config["player_workload"] = {
        "participant_scope": "confirmed_only",
        "minimum_rest_minutes": 60,
        "max_matches_per_day": 5,
    }
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "12:00"), assign(s, 2, "16:00")]
    assert not validate_schedule(s)
    s.config["runtime"]["schedule_objective"] = "compact_groups"
    r = solve_schedule(s, time_limit_seconds=5)
    assert successful(r), r
    assert not validate_schedule(s)
    starts = sorted(a.start_at for a in s.assignments)
    # 25 minutes play + 60 rest rounds to the next legal 15-minute grid slot.
    assert (starts[-1] - starts[0]).total_seconds() / 60 == 180
    assert r["diagnostics"]["objective"]["strategy"] == "compact_round_robin_groups"


def test_compact_mode_preserves_locked_assignments():
    s = fixture(shared=True, end="18:00")
    s.assignments = [
        assign(s, 0, "08:00", locks={"time": True, "court": True}),
        assign(s, 1, "16:00"),
    ]
    first = s.assignments[0].model_copy(deep=True)
    assert successful(solve_schedule(s, compact_groups=True))
    assert s.assignments[0] == first
    assert not validate_schedule(s)


def test_compact_mode_keeps_court_specific_availability():
    s = fixture(shared=True, courts=2, end="18:00")
    s.config["courts"][1]["availability"][0]["start_at"] = "2026-11-01T10:00:00+08:00"
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 5}
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "10:00", court=1)]
    assert successful(solve_schedule(s, compact_groups=True))
    assert not validate_schedule(s)


@pytest.mark.parametrize("daily_cap", [5, None])
def test_day_one_round_robin_priority_defers_selected_division_first(daily_cap):
    from datetime import timedelta

    from test_player_workload import two_days

    s = fixture(end="08:30")
    two_days(s)
    for stage in s.stages:
        stage.type = "round_robin"
    s.config["runtime"]["schedule_objective"] = "round_robin_first"
    s.config["runtime"]["day_two_priority_division_ids"] = [s.divisions[0].id]
    s.config["player_workload"] = {"max_matches_per_day": daily_cap, "minimum_rest_minutes": 60}
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "10:00")]
    s.assignments = [
        a.model_copy(
            update={
                key: getattr(a, key) + timedelta(days=1)
                for key in ["start_at", "play_end_at", "court_release_at"]
            }
        )
        for a in s.assignments
    ]
    r = solve_schedule(s, time_limit_seconds=5)
    assert successful(r), r
    assert not validate_schedule(s)
    assignments = {a.match_id: a for a in s.assignments}
    assert assignments[s.matches[1].id].start_at.day == 1
    assert assignments[s.matches[0].id].start_at.day == 2
    assert r["diagnostics"]["objective"]["strategy"] == "round_robin_first"
