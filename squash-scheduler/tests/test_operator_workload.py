"""Operator workload uses per-person compatible paths without mutating saved policy."""

from datetime import timedelta

import pytest
from test_qualification_paths import bracket
from test_scheduling import assign, fixture

from squash_scheduler.domain.models import Participant, content_hash, new_id
from squash_scheduler.operator_workload import build_operator_workload
from squash_scheduler.scheduling.paths import PathAnalysisIncomplete


def person(report, pid):
    return next(row for row in report["participants"] if row["participant_id"] == pid)


def test_cross_division_identity_zero_players_and_read_only_simulation():
    snapshot = fixture(shared=True, courts=2)
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "09:00", court=1)]
    before = content_hash(snapshot)
    report = build_operator_workload(
        snapshot, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 1}
    )
    assert content_hash(snapshot) == before
    assert report["mode"] == "simulation" and report["configured_policy"] == {}
    assert report["counts"] == {
        "participants": 4,
        "affected_participants": 1,
        "known_conflicts": 2,
        "possible_conflicts": 0,
        "scheduled_matches": 2,
        "unscheduled_matches": 0,
    }
    shared = person(report, snapshot.participants[0].id)
    assert len(shared["divisions"]) == 2
    assert shared["daily"][0]["known_count"] == 2
    assert shared["daily"][0]["maximum_path_count"] == 2
    assert shared["known_gaps"][0]["gap_minutes"] == 35
    assert shared["known_gaps"][0]["required_minutes"] == 60
    assert {c["code"] for c in shared["conflicts"]} == {
        "PLAYER_DAILY_LIMIT_EXCEEDED",
        "PLAYER_REST_VIOLATION",
    }
    for match in shared["daily"][0]["known_matches"]:
        assert {
            "match_id",
            "code",
            "division",
            "division_id",
            "start",
            "play_end",
            "court_id",
            "court",
            "status",
        } <= match.keys()
    zero = person(report, snapshot.participants[2].id)
    assert zero["daily"][0]["known_count"] == 0
    assert zero["daily"][0]["maximum_path_count"] == 0
    assert zero["conflict_count"] == 0


def test_configured_policy_remains_distinct_from_four_sixty_simulation():
    snapshot = fixture(shared=True, rest=15)
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "09:00")]
    before = content_hash(snapshot)
    configured = build_operator_workload(snapshot)
    assert configured["mode"] == "configured"
    assert configured["policy"] == {"minimum_rest_minutes": 15, "max_matches_per_day": None}
    assert configured["status"] == "passed"
    simulation = build_operator_workload(
        snapshot, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    )
    assert simulation["status"] == "violations"
    assert simulation["policy"]["minimum_rest_minutes"] == 60
    assert content_hash(snapshot) == before
    assert "player_workload" not in snapshot.config


@pytest.mark.parametrize("policy", [None, {"minimum_rest_minutes": 60, "max_matches_per_day": 2}])
def test_final_and_bronze_are_not_two_appearances_or_a_false_rest_conflict(policy):
    snapshot = bracket()
    snapshot.assignments = [
        assign(snapshot, 0, "08:00", court=0),
        assign(snapshot, 1, "08:00", court=1),
        assign(snapshot, 2, "09:30", court=0),
        assign(snapshot, 3, "09:30", court=1),
    ]
    report = build_operator_workload(snapshot, policy=policy)
    assert report["path_strategy"] == "qualification_paths"
    assert report["conflicts"] == []
    for row in report["participants"]:
        assert row["daily"][0]["known_count"] == 1
        assert row["daily"][0]["maximum_path_count"] == 2
        assert len(row["daily"][0]["candidate_matches"]) == 3


def test_possible_rest_conflicts_are_compatible_pairs_not_raw_adjacent_union():
    snapshot = bracket()
    snapshot.assignments = [
        assign(snapshot, 0, "08:00", court=0),
        assign(snapshot, 1, "08:00", court=1),
        assign(snapshot, 2, "08:30", court=0),
        assign(snapshot, 3, "08:30", court=1),
    ]
    report = build_operator_workload(
        snapshot, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    )
    final, bronze = snapshot.matches[-2:]
    assert report["counts"]["known_conflicts"] == 0
    assert report["counts"]["possible_conflicts"] == 8
    assert all(c["basis"] == "possible" for c in report["conflicts"])
    assert all(set(c["match_ids"]) != {final.id, bronze.id} for c in report["conflicts"])
    assert all(c["gap_minutes"] == 5 for c in report["conflicts"])


def test_sixty_minutes_is_inclusive_and_non_load_issues_are_separate():
    snapshot = fixture(shared=True)
    snapshot.assignments = [assign(snapshot, 0, "08:00"), assign(snapshot, 1, "09:25")]
    report = build_operator_workload(
        snapshot, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    )
    assert report["status"] == "passed"
    assert report["conflicts"] == []
    assert any(i["code"] == "START_GRID_VIOLATION" for i in report["global_issues"])
    assert person(report, snapshot.participants[0].id)["known_gaps"][0]["gap_minutes"] == 60


def test_person_override_does_not_turn_other_candidates_into_false_conflicts():
    snapshot = bracket(8)
    snapshot.participants[0].minimum_rest_minutes = 60
    semi = next(i for i, m in enumerate(snapshot.matches) if m.round == 2)
    final = next(
        i for i, m in enumerate(snapshot.matches) if m.round == 3 and m.kind != "placement"
    )
    snapshot.assignments = [
        assign(snapshot, semi, "08:00"),
        assign(snapshot, final, "08:30", court=1),
    ]
    report = build_operator_workload(snapshot)
    assert report["counts"]["possible_conflicts"] == 1
    assert report["conflicts"][0]["participant_ids"] == [snapshot.participants[0].id]
    assert report["conflicts"][0]["required_minutes"] == 60


def test_confirmed_four_allowed_five_flagged_and_zeroes_on_every_event_date():
    snapshot = fixture(end="18:00")
    snapshot.matches = [snapshot.matches[0].model_copy(update={"id": new_id()}) for _ in range(5)]
    snapshot.assignments = [
        assign(snapshot, i, value)
        for i, value in enumerate(["08:00", "09:30", "11:00", "12:30", "14:00"])
    ]
    snapshot.config["event"]["event_dates"].append("2026-11-02")
    snapshot.config["timing_defaults"]["daily_grid_anchors"].append(
        {"date": "2026-11-02", "local_time": "08:00"}
    )
    policy = {"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    report = build_operator_workload(snapshot, policy=policy)
    row = person(report, snapshot.participants[0].id)
    assert row["daily"][0]["known_exceeds_cap"]
    assert row["daily"][1]["known_count"] == 0 and row["daily"][1]["maximum_path_count"] == 0
    snapshot.matches.pop()
    snapshot.assignments.pop()
    report = build_operator_workload(snapshot, policy=policy)
    assert person(report, snapshot.participants[0].id)["daily"][0]["known_count"] == 4
    assert not report["conflicts"]


def test_partial_schedule_is_not_a_claim_of_complete_validity():
    snapshot = fixture(shared=True)
    snapshot.participants.append(Participant(official_name="Zero", display_name="Zero"))
    report = build_operator_workload(snapshot)
    assert report["status"] == "passed"  # assigned matches only
    assert report["assessment_scope"] == "scheduled_matches" and not report["schedule_complete"]
    assert report["counts"]["unscheduled_matches"] == 2
    assert len(report["participants"]) == 5
    assert all(row["daily"][0]["known_count"] == 0 for row in report["participants"])
    assert len(report["global_issues"]) == 2


def test_analysis_resource_failure_is_invalid_and_never_a_green_result(monkeypatch):
    from squash_scheduler import operator_workload

    snapshot = fixture()
    before = content_hash(snapshot)

    def fail(*args):
        raise PathAnalysisIncomplete("synthetic resource limit")

    monkeypatch.setattr(operator_workload, "analyze_paths", fail)
    report = build_operator_workload(snapshot)
    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "PLAYER_PATH_ANALYSIS_INCOMPLETE"
    assert content_hash(snapshot) == before


def test_known_rest_also_crosses_midnight():
    snapshot = fixture(shared=True)
    snapshot.assignments = [assign(snapshot, 0, "23:30"), assign(snapshot, 1, "00:30")]
    original = snapshot.assignments[1]
    snapshot.assignments[1] = original.model_copy(
        update={
            key: getattr(original, key) + timedelta(days=1)
            for key in ("start_at", "play_end_at", "court_release_at")
        }
    )
    report = build_operator_workload(
        snapshot, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    )
    gap = person(report, snapshot.participants[0].id)["known_gaps"][0]
    assert gap["gap_minutes"] == 35 and gap["violation"]
    assert gap["date"] == "2026-11-02"
    assert report["counts"]["known_conflicts"] == 1
