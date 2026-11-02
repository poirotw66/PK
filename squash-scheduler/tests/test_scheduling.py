"""Synthetic AT-15 through AT-23; no real event timing is inferred."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from squash_scheduler.domain.matches import round_robin
from squash_scheduler.domain.models import (
    Assignment,
    Division,
    Entry,
    EntrySide,
    Issue,
    Match,
    Participant,
    Pool,
    PoolRankSide,
    Registration,
    Snapshot,
    Stage,
    WinnerSide,
    new_id,
)
from squash_scheduler.scheduling import impacted_match_ids, solve_schedule, validate_schedule
from squash_scheduler.scheduling import solver as solver_module


def at(value: str, day: str = "2026-11-01") -> datetime:
    return datetime.fromisoformat(f"{day}T{value}:00+08:00")


def fixture(
    *, shared: bool = False, courts: int = 1, rest: int = 0, end: str = "12:00"
) -> Snapshot:
    people = [
        Participant(official_name=f"Person {i}", display_name=f"Person {i}") for i in range(4)
    ]
    divisions = [Division(name="A", status="active"), Division(name="B", status="active")]
    stages = [Stage(division_id=d.id, type="elimination") for d in divisions]
    registrations, entries, matches = [], [], []
    for index, person in enumerate(
        [people[0], people[1], people[0] if shared else people[2], people[3]]
    ):
        division = divisions[index // 2]
        registration = Registration(
            participant_id=person.id,
            original_division_id=division.id,
            source_ref={"file": "synthetic"},
        )
        entry = Entry(registration_id=registration.id, effective_division_id=division.id)
        registrations.append(registration)
        entries.append(entry)
    for index in range(2):
        matches.append(
            Match(
                division_id=divisions[index].id,
                stage_id=stages[index].id,
                side_a=EntrySide(entry_id=entries[2 * index].id),
                side_b=EntrySide(entry_id=entries[2 * index + 1].id),
            )
        )
    return Snapshot(
        config={
            "event": {"timezone": "Asia/Taipei", "event_dates": ["2026-11-01"]},
            "courts": [
                {
                    "id": new_id(),
                    "label": str(i),
                    "sort_order": i,
                    "availability": [
                        {"start_at": at("08:00").isoformat(), "end_at": at(end).isoformat()}
                    ],
                    "blackouts": [],
                }
                for i in range(courts)
            ],
            "timing_defaults": {
                "start_grid_minutes": 15,
                "daily_grid_anchors": [{"date": "2026-11-01", "local_time": "08:00"}],
                "estimated_play_minutes": 25,
                "court_buffer_minutes": 5,
                "minimum_player_rest_minutes": rest,
                "court_transfer_minutes": 0,
                "ranking_confirmation_minutes": 0,
            },
            "runtime": {"solver_time_limit_seconds": 5},
        },
        participants=people,
        registrations=registrations,
        entries=entries,
        divisions=divisions,
        stages=stages,
        matches=matches,
    )


def assign(
    snapshot: Snapshot,
    index: int,
    start: str,
    court: int = 0,
    play: int = 25,
    buffer: int = 5,
    locks: dict | None = None,
) -> Assignment:
    value = at(start)
    return Assignment(
        match_id=snapshot.matches[index].id,
        court_id=snapshot.config["courts"][court]["id"],
        start_at=value,
        play_end_at=value + timedelta(minutes=play),
        court_release_at=value + timedelta(minutes=play + buffer),
        locks=locks or {},
    )


def codes(snapshot: Snapshot, **kwargs) -> set[str]:
    return {i.code for i in validate_schedule(snapshot, **kwargs) if i.severity == "error"}


def successful(report: dict) -> bool:
    return report["status"] in {"FEASIBLE", "OPTIMAL"}


def test_at15_grid_is_independent_of_occupation():
    snapshot = fixture()
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "08:15")]
    assert "COURT_OVERLAP" in codes(snapshot)
    snapshot.assignments[1] = assign(snapshot, 1, "08:30")
    assert codes(snapshot) == set()
    snapshot.assignments[1] = assign(snapshot, 1, "08:31")
    assert "START_GRID_VIOLATION" in codes(snapshot)
    assert successful(solve_schedule(snapshot))
    assert codes(snapshot) == set()


def test_at16_cross_division_rest_and_person_override():
    snapshot = fixture(shared=True, rest=20)
    snapshot.config["timing_defaults"]["start_grid_minutes"] = 5
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "08:40")]
    assert "PLAYER_REST_VIOLATION" in codes(snapshot)
    snapshot.assignments[1] = assign(snapshot, 1, "08:45")
    assert codes(snapshot) == set()
    snapshot.participants[0].minimum_rest_minutes = 25
    assert "PLAYER_REST_VIOLATION" in codes(snapshot)
    report = solve_schedule(snapshot)
    assert successful(report)
    ordered = sorted(snapshot.assignments, key=lambda a: a.start_at)
    assert ordered[1].start_at >= ordered[0].play_end_at + timedelta(minutes=25)


def pooled_fixture() -> Snapshot:
    snapshot = fixture(courts=2)
    division = snapshot.divisions[0]
    snapshot.divisions = [division]
    for entry in snapshot.entries:
        entry.effective_division_id = division.id
    for registration in snapshot.registrations:
        registration.original_division_id = division.id
    pool_stage = Stage(division_id=division.id, type="round_robin")
    knockout = Stage(division_id=division.id, type="elimination", order=1)
    snapshot.stages = [pool_stage, knockout]
    pool = Pool(
        stage_id=pool_stage.id,
        label="A",
        entry_ids=[e.id for e in snapshot.entries[:3]],
        advancement_count=2,
    )
    snapshot.pools = [pool]
    snapshot.matches = round_robin(pool.entry_ids, division.id, pool_stage.id)
    snapshot.matches.append(
        Match(
            division_id=division.id,
            stage_id=knockout.id,
            side_a=PoolRankSide(pool_id=pool.id, rank=1),
            side_b=EntrySide(entry_id=snapshot.entries[3].id),
        )
    )
    return snapshot


def test_at17_unknown_qualifier_conservative_resource_constraints():
    snapshot = pooled_fixture()
    report = solve_schedule(snapshot)
    assert successful(report)
    assert report["diagnostics"]["candidate_conflict_strategy"] == "conservative"
    assert codes(snapshot) == set()
    # The rank side may be any of three people. It cannot overlap any pool match.
    qualifier = snapshot.assignments[-1]
    assert all(qualifier.start_at >= a.play_end_at for a in snapshot.assignments[:-1])


def test_at18_full_court_buffer_must_avoid_blackout():
    snapshot = fixture()
    snapshot.config["courts"][0]["blackouts"] = [
        {"start_at": at("08:28").isoformat(), "end_at": at("09:00").isoformat()}
    ]
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "09:00")]
    assert "COURT_UNAVAILABLE" in codes(snapshot)
    report = solve_schedule(snapshot)
    assert successful(report)
    assert all(a.start_at >= at("09:00") for a in snapshot.assignments)
    assert codes(snapshot) == set()


def test_at19_all_pool_games_and_ranking_confirmation_max_with_rest():
    snapshot = pooled_fixture()
    snapshot.config["timing_defaults"]["minimum_player_rest_minutes"] = 20
    snapshot.config["timing_defaults"]["ranking_confirmation_minutes"] = 30
    snapshot.config["timing_defaults"]["start_grid_minutes"] = 5
    # Pool games end at 08:25, 09:10 and 09:55. Ranking confirmation and rest
    # overlap, so 10:25 is valid (adding the waits would incorrectly require 10:45).
    snapshot.assignments = [
        assign(snapshot, 0, "08:00"),
        assign(snapshot, 1, "08:45"),
        assign(snapshot, 2, "09:30"),
        assign(snapshot, 3, "10:25", court=1),
    ]
    assert codes(snapshot) == set()
    snapshot.assignments[-1] = assign(snapshot, 3, "10:15", court=1)
    assert "ADVANCEMENT_NOT_READY" in codes(snapshot)
    assert successful(solve_schedule(snapshot))
    assert codes(snapshot) == set()


def test_at20_exhibition_shares_player_and_court_constraints():
    snapshot = fixture(shared=True, courts=2, rest=20)
    snapshot.matches[1].kind = "exhibition"
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "08:00", court=1)]
    assert "PLAYER_REST_VIOLATION" in codes(snapshot)
    assert successful(solve_schedule(snapshot))
    assert codes(snapshot) == set()


def test_at21_locks_local_boundary_and_expansion_proposal():
    snapshot = fixture(courts=2, end="09:00")
    snapshot.assignments = [
        assign(snapshot, 0, "08:00", locks={"court": True}),
        assign(snapshot, 1, "08:30"),
    ]
    snapshot.config["courts"][0]["blackouts"] = [
        {"start_at": at("08:00").isoformat(), "end_at": at("08:30").isoformat()}
    ]
    snapshot.config["courts"][1]["availability"][0]["end_at"] = at("08:30").isoformat()
    before = snapshot.model_dump(mode="json")
    report = solve_schedule(snapshot, local_match_ids={snapshot.matches[0].id})
    assert report["status"] == "INFEASIBLE"
    assert snapshot.model_dump(mode="json") == before
    proposal = report["diagnostics"]["expansion_proposal"]
    assert proposal["status"] == "proposal_only"
    assert snapshot.matches[1].id in proposal["additional_match_ids"]
    assert successful(solve_schedule(snapshot))
    assert snapshot.assignments[0].court_id == snapshot.config["courts"][0]["id"]
    assert codes(snapshot) == set()


@pytest.mark.parametrize("limit", [0, 1e-9])
def test_at22_timeout_is_unknown_not_infeasible(limit):
    snapshot = fixture()
    before = snapshot.model_dump(mode="json")
    report = solve_schedule(snapshot, time_limit_seconds=limit)
    assert report["status"] == "UNKNOWN"
    assert report["assignments"] == []
    assert snapshot.model_dump(mode="json") == before


def test_at23_independent_validator_can_reject_solver_incumbent(monkeypatch):
    snapshot = fixture()
    before = snapshot.model_dump(mode="json")
    observed = []

    def reject(candidate, *, baseline_assignments=None):
        observed.append(candidate)
        return [
            Issue(code="TEST_INDEPENDENT_REJECTION", gate="G4", message="Injected verifier failure")
        ]

    monkeypatch.setattr(solver_module, "validate_schedule", reject)
    report = solve_schedule(snapshot)
    assert observed and observed[0].assignments
    assert report["status"] == "MODEL_INVALID"
    assert report["diagnostics"]["solver_status"] in {"FEASIBLE", "OPTIMAL"}
    assert report["diagnostics"]["independent_validation"] == "failed"
    assert report["assignments"] == []
    assert snapshot.model_dump(mode="json") == before


def test_null_timing_is_blocking_and_unchanged():
    snapshot = fixture()
    for key in snapshot.config["timing_defaults"]:
        snapshot.config["timing_defaults"][key] = None
    before = snapshot.model_dump(mode="json")
    report = solve_schedule(snapshot)
    assert report["status"] == "MODEL_INVALID"
    assert "TIMING_CONFIG_MISSING" in {i["code"] for i in report["issues"]}
    assert snapshot.model_dump(mode="json") == before


def test_stage_and_division_overrides_resolve_before_constraint_checks():
    snapshot = fixture(shared=True)
    snapshot.divisions[1].rule_overrides = {
        "timing": {"estimated_play_minutes": 35, "minimum_player_rest_minutes": 20}
    }
    snapshot.stages[1].rule_set_id = "long-match"
    snapshot.config["rule_sets"] = {"long-match": {"timing": {"estimated_play_minutes": 40}}}
    assert successful(solve_schedule(snapshot))
    assert snapshot.assignments[1].play_end_at - snapshot.assignments[1].start_at == timedelta(
        minutes=40
    )
    assert codes(snapshot) == set()


def test_started_matches_and_time_locks_are_immutable():
    snapshot = fixture()
    snapshot.assignments = [
        assign(snapshot, 0, "09:00", locks={"time": True}),
        assign(snapshot, 1, "10:00"),
    ]
    snapshot.matches[1].status = "completed"
    baseline = [a.model_copy(deep=True) for a in snapshot.assignments]
    assert successful(solve_schedule(snapshot))
    assert snapshot.assignments[0].start_at == at("09:00")
    assert snapshot.assignments[1] == baseline[1]
    snapshot.assignments[1] = assign(snapshot, 1, "10:30")
    assert "LOCK_VIOLATION" in codes(snapshot, baseline_assignments=baseline)


def test_transfer_time_and_rest_use_maximum_rules_without_court_buffer():
    snapshot = fixture(shared=True, courts=2, rest=20)
    snapshot.config["timing_defaults"]["court_transfer_minutes"] = 10
    snapshot.config["timing_defaults"]["start_grid_minutes"] = 5
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "08:50", court=1)]
    assert "PLAYER_REST_VIOLATION" in codes(snapshot)
    snapshot.assignments[1] = assign(snapshot, 1, "08:55", court=1)
    assert codes(snapshot) == set()
    snapshot.assignments[1] = assign(snapshot, 1, "08:45", court=0)
    assert codes(snapshot) == set()


def test_bad_graph_is_rejected_before_candidate_expansion():
    snapshot = fixture()
    snapshot.matches[0].side_a = WinnerSide(match_id=snapshot.matches[0].id)
    report = solve_schedule(snapshot)
    assert report["status"] == "MODEL_INVALID"
    assert "MATCH_GRAPH_CYCLE" in {i["code"] for i in report["issues"]}


def test_capacity_failure_has_related_resources_and_mutable_parameters():
    snapshot = fixture(end="08:30")
    report = solve_schedule(snapshot)
    assert report["status"] == "INFEASIBLE"
    assert "COURT_CAPACITY_INSUFFICIENT" in {i["code"] for i in report["issues"]}
    assert report["diagnostics"]["match_ids"] == sorted(m.id for m in snapshot.matches)
    assert report["diagnostics"]["court_ids"]
    assert report["diagnostics"]["modifiable_parameters"]


def test_unknown_duplicate_missing_and_wrong_duration_assignments_are_rejected():
    snapshot = fixture()
    first = assign(snapshot, 0, "08:00", play=20, buffer=10)
    snapshot.assignments = [first, first.model_copy(deep=True)]
    assert {"ASSIGNMENT_DURATION", "ASSIGNMENT_DUPLICATE", "ASSIGNMENT_MISSING"} <= codes(snapshot)
    snapshot.assignments[1] = Assignment(
        match_id=new_id(),
        court_id=new_id(),
        start_at=at("09:00"),
        play_end_at=at("09:25"),
        court_release_at=at("09:30"),
    )
    assert "ASSIGNMENT_INACTIVE" in codes(snapshot)


def test_daily_anchor_and_allowed_event_dates_are_enforced():
    snapshot = fixture()
    snapshot.config["timing_defaults"]["daily_grid_anchors"][0]["local_time"] = "08:05"
    assert successful(solve_schedule(snapshot))
    assert all(
        a.start_at.astimezone(at("08:00").tzinfo).minute % 15 == 5 for a in snapshot.assignments
    )
    assert codes(snapshot) == set()
    invalid = snapshot.assignments[0].model_copy(
        update={
            "start_at": at("08:05", "2026-11-02"),
            "play_end_at": at("08:30", "2026-11-02"),
            "court_release_at": at("08:35", "2026-11-02"),
        }
    )
    snapshot.assignments[0] = invalid
    assert {"START_GRID_VIOLATION", "COURT_UNAVAILABLE"} <= codes(snapshot)


def test_impact_closure_tracks_possible_players_dependencies_and_actual_court_overlap():
    snapshot = fixture()
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "08:30")]
    seed = {snapshot.matches[0].id}
    assert impacted_match_ids(snapshot, seed) == seed
    snapshot.assignments[1] = assign(snapshot, 1, "08:15")
    assert impacted_match_ids(snapshot, seed) == {m.id for m in snapshot.matches}
    snapshot = pooled_fixture()
    assert impacted_match_ids(snapshot, {snapshot.matches[-1].id}) == {
        m.id for m in snapshot.matches
    }


def test_successful_local_solve_preserves_unaffected_assignments_exactly():
    snapshot = fixture(courts=2)
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "10:00", court=1)]
    untouched = snapshot.assignments[1].model_copy(deep=True)
    snapshot.config["courts"][0]["blackouts"] = [
        {"start_at": at("08:00").isoformat(), "end_at": at("09:00").isoformat()}
    ]
    report = solve_schedule(snapshot, local_match_ids={snapshot.matches[0].id})
    assert successful(report)
    assert snapshot.assignments[1] == untouched
    assert codes(snapshot) == set()
