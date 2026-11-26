"""Returned court facts remain visible and constrain local recovery and replanning."""

from datetime import datetime, timedelta

from test_cloud_handoff import apply, finish, returned, seal
from test_cloud_handoff import service as service  # noqa: PLC0414

import squash_scheduler.cloud_handoff as handoff
from squash_scheduler.operator_actions import schedule_live_options
from squash_scheduler.public_feed import project_snapshot
from squash_scheduler.scheduling.constraints import build_context


def test_returned_actual_start_and_court_reach_operator_board_and_solver(service, monkeypatch):
    envelope = finish(returned(service))
    match = envelope["state"]["matches"][0]
    planned = datetime.fromtimestamp(match["assignment"]["start"] * 60).astimezone()
    started = planned + timedelta(minutes=3, seconds=12)
    exported = started + timedelta(minutes=30, seconds=5)
    envelope["live"]["starts"][match["id"]] = started.isoformat()
    actual_court = next(
        court["id"]
        for court in envelope["state"]["courts"]
        if court["id"] != match["assignment"]["court"]
    )
    envelope["live"]["actual_courts"][match["id"]] = actual_court
    envelope["exported_at"] = exported.isoformat()
    monkeypatch.setattr(handoff, "utcnow", lambda: exported)
    before = service.repo.load()
    apply(service, "cloud_return", envelope=seal(envelope))
    saved = service.repo.load()
    assert saved.assignments == before.assignments
    board = service.state()["live_board"]["courts"]
    court = next(
        court for court in board if any(row["match_id"] == match["id"] for row in court["matches"])
    )
    row = next(row for row in court["matches"] if row["match_id"] == match["id"])
    assert court["id"] == actual_court
    assert row["actual_court_id"] == actual_court
    assert row["planned_court_id"] == match["assignment"]["court"]
    assert row["started_at"] == started.isoformat()
    assert row["status"] == "completed"
    public = project_snapshot(saved, "synthetic-workspace")
    visible = next(item for item in public.matches if item.id == match["id"])
    assert visible.court_id == actual_court
    assert datetime.fromisoformat(visible.started_at) == started
    options = schedule_live_options(saved, at=started.replace(second=0, microsecond=0))
    rounded_export = exported.replace(second=0, microsecond=0) + timedelta(minutes=1)
    context, issues = build_context(saved)
    assert context is not None and not issues
    occupancy = options["blocked_court_intervals"][actual_court]
    assert occupancy == [
        (
            started.replace(second=0, microsecond=0),
            rounded_export + timedelta(minutes=match["buffer"]),
        )
    ]
    assert match["assignment"]["court"] not in options["blocked_court_intervals"]
    shared = next(
        mid
        for mid in options["not_before_by_match"]
        if context.candidates[mid] & context.candidates[match["id"]]
    )
    assert options["not_before_by_match"][shared] >= rounded_export + timedelta(minutes=60)


def test_later_return_does_not_delay_completion_bound_again(service, monkeypatch):
    envelope = finish(returned(service))
    started = datetime.fromisoformat(next(iter(envelope["live"]["starts"].values())))
    envelope["exported_at"] = (started + timedelta(minutes=30)).isoformat()
    monkeypatch.setattr(handoff, "utcnow", lambda: started + timedelta(hours=2))
    apply(service, "cloud_return", envelope=seal(envelope))
    original = schedule_live_options(service.repo.load(), at=started)
    envelope.update(
        cloud_revision="synthetic-later-cloud",
        sequence=2,
        exported_at=(started + timedelta(hours=1)).isoformat(),
    )
    apply(service, "cloud_return", envelope=seal(envelope))
    assert schedule_live_options(service.repo.load(), at=started) == original
