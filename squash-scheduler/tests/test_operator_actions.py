"""Human court facts and entry reviews remain exact, reversible draft operations."""

from datetime import timedelta

import pytest
from test_operator import operator_snapshot
from test_withdrawals import replacement

import squash_scheduler.operator_actions as actions
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import EntrySide, new_id
from squash_scheduler.domain.rules import refresh_rule_snapshots
from squash_scheduler.scheduling.constraints import validate_schedule
from squash_scheduler.scheduling.solver import solve_schedule


@pytest.fixture
def scheduled(monkeypatch):
    snapshot, _ = operator_snapshot()
    generate_matches(snapshot)
    report = solve_schedule(snapshot, time_limit_seconds=3)
    assert report["status"] in {"FEASIBLE", "OPTIMAL"}, report
    first = min(snapshot.assignments, key=lambda a: a.start_at)
    monkeypatch.setattr(actions, "utcnow", lambda: first.start_at)
    return snapshot, first


def perform(snapshot, **operation):
    prepared = actions.prepare_action(
        snapshot, {"actor": "Justin", "reason": "Synthetic reviewed operation", **operation}
    )
    return actions.execute_action(snapshot, prepared)


def test_clock_never_starts_match_and_preview_is_repeatable(scheduled):
    snapshot, first = scheduled
    before = snapshot.model_dump(mode="json")
    assert actions.schedule_live_options(snapshot) == {}
    state = actions.state_fields(snapshot)
    rows = [m for c in state["live_board"]["courts"] for m in c["matches"]]
    assert all(m["started_at"] is None and m["status"] == "planned" for m in rows)
    operation = actions.prepare_action(
        snapshot,
        {
            "action": "live_match",
            "event": "call",
            "match_id": first.match_id,
            "actor": "Justin",
            "reason": "Ready",
        },
    )
    one, summary = actions.execute_action(snapshot, operation)
    two, repeated = actions.execute_action(snapshot, operation)
    assert summary == repeated
    assert one.changes[-1]["id"] != two.changes[-1]["id"]
    assert next(m for m in one.matches if m.id == first.match_id).status == "ready"
    assert snapshot.model_dump(mode="json") == before
    started, _ = perform(one, action="live_match", event="start", match_id=first.match_id)
    assert next(m for m in started.matches if m.id == first.match_id).status == "in_progress"
    assert started.assignments == snapshot.assignments
    rows = [m for c in actions.state_fields(started)["live_board"]["courts"] for m in c["matches"]]
    assert (
        next(m for m in rows if m["match_id"] == first.match_id)["started_at"]
        == first.start_at.isoformat()
    )


def test_live_time_is_frozen_and_stale_preview_rejected(scheduled, monkeypatch):
    snapshot, first = scheduled
    operation = actions.prepare_action(
        snapshot,
        {
            "action": "live_match",
            "event": "start",
            "match_id": first.match_id,
            "actor": "Justin",
            "reason": "Ready",
        },
    )
    monkeypatch.setattr(actions, "utcnow", lambda: first.start_at + timedelta(minutes=3))
    with pytest.raises(ValueError, match="兩分鐘"):
        actions.execute_action(snapshot, operation)


def test_start_rejects_early_and_unassigned(scheduled, monkeypatch):
    snapshot, first = scheduled
    monkeypatch.setattr(actions, "utcnow", lambda: first.start_at - timedelta(minutes=1))
    with pytest.raises(ValueError, match="預定"):
        perform(snapshot, action="live_match", event="start", match_id=first.match_id)
    snapshot.assignments = []
    with pytest.raises(ValueError, match="排程"):
        perform(snapshot, action="live_match", event="call", match_id=first.match_id)


def test_in_progress_court_rejects_call_and_repeated_start(scheduled):
    snapshot, first = scheduled
    started, _ = perform(snapshot, action="live_match", event="start", match_id=first.match_id)
    with pytest.raises(ValueError, match="已開賽"):
        perform(started, action="live_match", event="start", match_id=first.match_id)
    later = next(
        a
        for a in started.assignments
        if a.court_id == first.court_id and a.match_id != first.match_id
    )
    with pytest.raises(ValueError, match="場地已有|對戰雙方"):
        perform(started, action="live_match", event="call", match_id=later.match_id)


@pytest.mark.parametrize("minutes", [0, -1, True, 1.5, 1441])
def test_delay_requires_positive_integer(scheduled, minutes):
    snapshot, first = scheduled
    with pytest.raises(ValueError, match="整數"):
        perform(
            snapshot,
            action="live_match",
            event="delay",
            match_id=first.match_id,
            delay_minutes=minutes,
        )


def test_delay_replan_is_exact_and_preserves_locked_or_started_assignments(scheduled):
    snapshot, first = scheduled
    delayed, _ = perform(
        snapshot, action="live_match", event="delay", match_id=first.match_id, delay_minutes=15
    )
    change_id = delayed.changes[-1]["id"]
    assert actions.validate_live_schedule(delayed)
    before = delayed.model_dump(mode="json")
    operation = actions.prepare_action(
        delayed,
        {
            "action": "local_replan",
            "change_id": change_id,
            "actor": "Justin",
            "reason": "Delay scope reviewed",
        },
    )
    assert delayed.model_dump(mode="json") == before
    revised, summary = actions.execute_action(delayed, operation)
    assert summary["assignments"] == operation["assignments"]
    assert not validate_schedule(revised)
    assert not actions.validate_live_schedule(revised)
    new_first = next(a for a in revised.assignments if a.match_id == first.match_id)
    assert new_first.start_at >= first.start_at + timedelta(minutes=15)
    # Replanning again does not repeatedly add the same delay.
    row = next(
        m
        for c in actions.state_fields(revised)["live_board"]["courts"]
        for m in c["matches"]
        if m["match_id"] == first.match_id
    )
    assert row["earliest_start_at"] == (first.start_at + timedelta(minutes=15)).isoformat()
    tampered = dict(operation)
    tampered["assignments"] = [a.model_dump(mode="json") for a in delayed.assignments]
    with pytest.raises(ValueError, match="驗證失敗"):
        actions.execute_action(delayed, tampered)


def test_live_court_block_does_not_remove_running_assignment(scheduled):
    snapshot, first = scheduled
    started, _ = perform(snapshot, action="live_match", event="start", match_id=first.match_id)
    result = solve_schedule(started, time_limit_seconds=3, **actions.schedule_live_options(started))
    assert result["status"] in {"FEASIBLE", "OPTIMAL"}, result
    assert next(a for a in started.assignments if a.match_id == first.match_id) == first
    assert not actions.validate_live_schedule(started)


def test_restore_cutoff_and_changed_opponents_discard_old_live_projection(scheduled):
    snapshot, first = scheduled
    delayed, _ = perform(
        snapshot, action="live_match", event="delay", match_id=first.match_id, delay_minutes=15
    )
    match = next(m for m in delayed.matches if m.id == first.match_id)
    match.side_a = EntrySide(entry_id=new_id())
    assert actions.schedule_live_options(delayed) == {}
    delayed.changes.append({"kind": "restore_revision", "id": new_id()})
    assert actions.schedule_live_options(delayed) == {}


def test_reserve_and_replacement_require_independent_review(scheduled):
    snapshot, first = scheduled
    reserve = replacement(snapshot, snapshot.divisions[0])
    source = snapshot.sources[0]["id"]
    next(r for r in snapshot.registrations if r.id == reserve.registration_id).source_ref[
        "source_file_id"
    ] = source
    reserved, _ = perform(snapshot, action="reserve", entry_id=reserve.id)
    assert actions.state_fields(reserved)["reserve_entry_ids"] == [reserve.id]
    match = next(m for m in reserved.matches if m.id == first.match_id)
    entry_id = match.side_a.entry_id
    changed, summary = perform(
        reserved,
        action="entry_change",
        entry_id=entry_id,
        kind="replace",
        replacement_entry_id=reserve.id,
    )
    assert summary["impact"]["rebound_match_ids"]
    change_id = changed.changes[-1]["id"]
    target = actions.state_fields(changed)["entry_review_targets"][-1]
    assert target["status"] == "draft"
    assert actions.state_fields(changed)["reserve_entry_ids"] == []
    with pytest.raises(ValueError, match="核准"):
        perform(changed, action="local_replan", change_id=change_id)
    approved, _ = perform(changed, action="approve_entry_change", change_id=change_id)
    assert actions.state_fields(approved)["entry_review_targets"][-1]["status"] == "approved"
    replanned, summary = perform(approved, action="local_replan", change_id=change_id)
    assert not validate_schedule(replanned)
    assert summary["assignments"]


def test_replacement_preserves_side_lock(scheduled):
    snapshot, first = scheduled
    reserve = replacement(snapshot, snapshot.divisions[0])
    first.locks["sides"] = True
    match = next(m for m in snapshot.matches if m.id == first.match_id)
    with pytest.raises(ValueError, match="lock"):
        perform(
            snapshot,
            action="entry_change",
            entry_id=match.side_a.entry_id,
            kind="replace",
            replacement_entry_id=reserve.id,
        )


def test_delay_clear_and_actual_start_check_court_window(scheduled, monkeypatch):
    snapshot, first = scheduled
    delayed, _ = perform(
        snapshot, action="live_match", event="delay", match_id=first.match_id, delay_minutes=15
    )
    with pytest.raises(ValueError, match="延誤後"):
        perform(delayed, action="live_match", event="start", match_id=first.match_id)
    cleared, _ = perform(delayed, action="live_match", event="clear_delay", match_id=first.match_id)
    assert not actions._live(cleared)[first.match_id]["delay_active"]
    monkeypatch.setattr(actions, "utcnow", lambda: first.start_at + timedelta(days=1))
    with pytest.raises(ValueError, match="場地可用"):
        perform(cleared, action="live_match", event="start", match_id=first.match_id)


def test_local_replan_does_not_override_time_lock(scheduled):
    snapshot, first = scheduled
    first.locks["time"] = True
    delayed, _ = perform(
        snapshot, action="live_match", event="delay", match_id=first.match_id, delay_minutes=15
    )
    with pytest.raises(ValueError, match="無可用解"):
        perform(delayed, action="local_replan", change_id=delayed.changes[-1]["id"])
    assert (
        next(a for a in delayed.assignments if a.match_id == first.match_id).start_at
        == first.start_at
    )


def test_withdrawal_review_preserves_confirmed_walkover_and_other_roster(scheduled):
    snapshot, first = scheduled
    snapshot.config["match_rule_defaults"]["walkover_rule"] = {
        "include_in_rankings": True,
        "counts_as_played": False,
        "winner_points": 2,
        "loser_points": 0,
    }
    snapshot.config["match_rule_defaults"]["ranking_denominator_rule"] = {"walkover": True}
    generate_matches(snapshot)
    refresh_rule_snapshots(snapshot)
    match = next(m for m in snapshot.matches if m.id == first.match_id)
    changed, summary = perform(
        snapshot, action="entry_change", entry_id=match.side_a.entry_id, kind="withdraw"
    )
    assert summary["impact"]["walkover_match_ids"] == [match.id]
    assert next(m for m in changed.matches if m.id == match.id).status == "completed"
    evidence = changed.results.copy()
    approved, _ = perform(
        changed, action="approve_entry_change", change_id=changed.changes[-1]["id"]
    )
    revised, _ = perform(approved, action="local_replan", change_id=changed.changes[-1]["id"])
    assert revised.results == evidence
    assert revised.registrations == snapshot.registrations
    assert next(a for a in revised.assignments if a.match_id == first.match_id) == first


def test_solver_rejects_invalid_live_limits_without_mutating(scheduled):
    snapshot, first = scheduled
    before = snapshot.model_dump(mode="json")
    report = solve_schedule(snapshot, not_before_by_match={new_id(): first.start_at})
    assert report["status"] == "MODEL_INVALID"
    assert snapshot.model_dump(mode="json") == before
