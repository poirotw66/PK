"""Cross-division daily caps and an event-wide rest floor, including failure safety."""

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from test_scheduling import assign, at, fixture, successful

from squash_scheduler.cli import main
from squash_scheduler.domain.config import PlayerWorkload
from squash_scheduler.domain.models import content_hash, new_id
from squash_scheduler.scheduling import solve_schedule, validate_schedule
from squash_scheduler.scheduling.workload import audit_workload
from squash_scheduler.storage.repository import Repository


def two_days(snapshot):
    snapshot.config["event"]["event_dates"].append("2026-11-02")
    snapshot.config["timing_defaults"]["daily_grid_anchors"].append(
        {"date": "2026-11-02", "local_time": "08:00"}
    )
    for court in snapshot.config["courts"]:
        court["availability"].append(
            {
                "start_at": at("08:00", "2026-11-02").isoformat(),
                "end_at": at("12:00", "2026-11-02").isoformat(),
            }
        )


def test_daily_cap_merges_cross_division_registrations_and_separates_local_dates():
    s = fixture(shared=True, courts=2)
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 1}
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "10:00", court=1)]
    assert "PLAYER_DAILY_LIMIT_EXCEEDED" in {i.code for i in validate_schedule(s)}
    before = content_hash(s)
    report = solve_schedule(s)
    assert report["status"] == "INFEASIBLE"
    assert content_hash(s) == before
    two_days(s)
    assert successful(solve_schedule(s))
    assert not validate_schedule(s)
    assert len({a.start_at.astimezone(ZoneInfo("Asia/Taipei")).date() for a in s.assignments}) == 2


def test_rest_floor_applies_even_to_older_rule_snapshots_and_uses_play_end():
    s = fixture(shared=True, rest=0)
    for m in s.matches:
        m.rule_snapshot = {"timing": dict(s.config["timing_defaults"])}
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    # Starts 60 minutes apart, but 25 minutes of play leaves only 35 minutes rest.
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "09:00")]
    assert "PLAYER_REST_VIOLATION" in {i.code for i in validate_schedule(s)}
    assert successful(solve_schedule(s))
    a, b = sorted(s.assignments, key=lambda a: a.start_at)
    assert b.start_at - a.play_end_at >= timedelta(minutes=60)
    assert not validate_schedule(s)


def test_audit_is_read_only_and_lists_names_match_ids_and_rest_failures():
    s = fixture(shared=True)
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "09:00")]
    before = content_hash(s)
    report = audit_workload(s, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 1})
    assert content_hash(s) == before
    assert report["status"] == "violations"
    row = next(r for r in report["daily"] if r["participant_id"] == s.participants[0].id)
    assert row["known_count"] == 2 and row["known_exceeds_cap"]
    assert len(row["known_matches"]) == 2
    assert any(
        g["basis"] == "known" and g["gap_minutes"] == 35 and g["violation"] for g in report["gaps"]
    )


@pytest.mark.parametrize(
    "policy",
    [
        {"max_matches_per_day": 0},
        {"max_matches_per_day": True},
        {"minimum_rest_minutes": -1},
        {"minimum_rest_minutes": "60"},
    ],
)
def test_invalid_limits_are_rejected_in_config_and_solver(policy):
    with pytest.raises(ValidationError):
        PlayerWorkload.model_validate(policy)
    s = fixture()
    s.config["player_workload"] = policy
    assert solve_schedule(s)["status"] == "MODEL_INVALID"


def test_daily_cap_counts_candidates_once_per_person_but_labels_bound_honestly():
    s = fixture(shared=True)
    s.config["player_workload"] = {"max_matches_per_day": 1}
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "10:00")]
    report = audit_workload(s)
    row = next(r for r in report["daily"] if r["participant_id"] == s.participants[0].id)
    assert row["candidate_upper_bound"] == 2
    assert report["issues"][0]["code"] == "PLAYER_DAILY_LIMIT_EXCEEDED"
    assert any("互斥" in note for note in report["notes"])


def test_four_matches_allowed_fifth_rejected_and_locked_assignments_not_silently_moved():
    s = fixture(shared=True, end="18:00")
    s.matches = [s.matches[0].model_copy(update={"id": new_id()}) for _ in range(5)]
    s.config["player_workload"] = {"max_matches_per_day": 4, "minimum_rest_minutes": 60}
    s.assignments = [
        assign(s, i, start, locks={"time": True})
        for i, start in enumerate(["08:00", "09:30", "11:00", "12:30", "14:00"])
    ]
    last_match = s.matches.pop()
    last = s.assignments.pop()
    assert not validate_schedule(s)
    s.matches.append(last_match)
    s.assignments.append(last)
    assert "PLAYER_DAILY_LIMIT_EXCEEDED" in {i.code for i in validate_schedule(s)}
    two_days(s)
    before = content_hash(s)
    result = solve_schedule(s)
    assert result["status"] == "INFEASIBLE"
    assert content_hash(s) == before


def test_audit_cli_checks_requested_limits_without_saving_policy_or_a_revision(tmp_path, capsys):
    s = fixture(shared=True)
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "09:00")]
    repo = Repository(tmp_path / "event")
    with repo.lock():
        original = repo.save(s, None)
    output = tmp_path / "audit.json"
    code = main(
        [
            "schedule",
            "audit",
            "--workspace",
            str(repo.root),
            "--minimum-rest",
            "60",
            "--max-matches-per-day",
            "1",
            "--output",
            str(output),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 3 and report["status"] == "violations"
    assert json.loads(output.read_text("utf-8"))["policy"]["minimum_rest_minutes"] == 60
    assert content_hash(repo.load()) == content_hash(original)


def test_failed_solve_cli_leaves_original_policy_and_schedule_unchanged(tmp_path, capsys):
    from test_operator import operator_snapshot

    from squash_scheduler.domain.matches import generate_matches

    s, _ = operator_snapshot()
    generate_matches(s)
    assert successful(solve_schedule(s))
    repo = Repository(tmp_path / "event")
    with repo.lock():
        original = repo.save(s, None)
    code = main(
        [
            "schedule",
            "solve",
            "--workspace",
            str(repo.root),
            "--actor",
            "Justin",
            "--minimum-rest",
            "60",
            "--max-matches-per-day",
            "1",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 5 and report["status"] == "INFEASIBLE"
    assert content_hash(repo.load()) == content_hash(original)


def test_actual_start_counts_actual_day_when_prior_match_slipped_to_next_day(monkeypatch):
    from test_operator_actions import perform

    import squash_scheduler.operator_actions as actions

    s = fixture(shared=True)
    two_days(s)
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 1}
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "10:00")]
    s.assignments[1] = s.assignments[1].model_copy(
        update={
            key: getattr(s.assignments[1], key) + timedelta(days=1)
            for key in ("start_at", "play_end_at", "court_release_at")
        }
    )
    s.matches[0].status = "completed"
    assert not validate_schedule(s)
    # The first match actually slipped into day two; do not trust its old scheduled date.
    monkeypatch.setattr(
        actions,
        "_live",
        lambda _: {s.matches[0].id: {"started_at": at("08:00", "2026-11-02").isoformat()}},
    )
    monkeypatch.setattr(actions, "_graph", lambda _: None)
    monkeypatch.setattr(actions, "utcnow", lambda: at("10:00", "2026-11-02"))
    with pytest.raises(ValueError, match="當日已達"):
        perform(s, action="live_match", event="start", match_id=s.matches[1].id)


def test_actual_completion_rounds_up_to_avoid_shortening_rest():
    import squash_scheduler.operator_actions as actions
    from squash_scheduler.scheduling.constraints import build_context

    s = fixture(shared=True)
    s.matches[0].status = "completed"
    s.results = [
        {
            "match_id": s.matches[0].id,
            "status": "confirmed",
            "created_at": "2026-11-01T09:00:59+08:00",
        }
    ]
    context, _ = build_context(s)
    end = actions._actual_end(
        s, s.matches[0].id, {"started_at": at("08:00").isoformat()}, at("10:00"), context
    )
    assert end == at("09:01")


def test_rest_audit_also_checks_the_gap_across_midnight():
    s = fixture(shared=True)
    two_days(s)
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    first = assign(s, 0, "23:30")
    second = assign(s, 1, "00:30")
    second = second.model_copy(
        update={
            key: getattr(second, key) + timedelta(days=1)
            for key in ("start_at", "play_end_at", "court_release_at")
        }
    )
    s.assignments = [first, second]
    report = audit_workload(s)
    assert any(
        g["basis"] == "known" and g["gap_minutes"] == 35 and g["violation"] for g in report["gaps"]
    )


def test_confirmed_scope_keeps_known_rest_and_caps():
    s = fixture(shared=True)
    s.config["player_workload"] = {
        "participant_scope": "confirmed_only",
        "minimum_rest_minutes": 60,
        "max_matches_per_day": 1,
    }
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "09:00")]
    codes = {i.code for i in validate_schedule(s)}
    assert {"PLAYER_REST_VIOLATION", "PLAYER_DAILY_LIMIT_EXCEEDED"} <= codes
    assert solve_schedule(s)["status"] == "INFEASIBLE"


def test_confirmed_scope_rechecks_newly_resolved_qualification():
    from squash_scheduler.domain.models import LoserSide, WinnerSide
    from squash_scheduler.scheduling.constraints import build_context

    assert PlayerWorkload().participant_scope == "all_possible"
    s = fixture()
    first = s.matches[0]
    s.matches = [
        first,
        first.model_copy(
            update={
                "id": new_id(),
                "side_a": WinnerSide(match_id=first.id),
                "side_b": LoserSide(match_id=first.id),
            }
        ),
    ]
    # A downstream match with unresolved sides has no confirmed appearances.
    s.config["player_workload"] = {"participant_scope": "confirmed_only", "max_matches_per_day": 1}
    context, issues = build_context(s)
    assert context is not None, issues
    assert context.candidates[s.matches[1].id] == set()
    s.results = [
        {
            "match_id": first.id,
            "status": "confirmed",
            "outcome": "played",
            "winner_id": first.side_a.entry_id,
        }
    ]
    context, issues = build_context(s)
    assert context is not None, issues
    assert context.candidates[s.matches[1].id] == {s.participants[0].id, s.participants[1].id}
    s.assignments = [assign(s, 0, "08:00"), assign(s, 1, "10:00")]
    assert "PLAYER_DAILY_LIMIT_EXCEEDED" in {i.code for i in validate_schedule(s)}
