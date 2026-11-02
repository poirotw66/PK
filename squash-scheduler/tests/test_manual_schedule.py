"""Manual batch editing is previewable even when invalid, and never bypasses gates."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from test_operator import confirm, operator_snapshot, preview

from squash_scheduler.domain.models import content_hash
from squash_scheduler.operations import Conflict, OperatorService
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def scheduled(tmp_path):
    from squash_scheduler.domain.matches import generate_matches

    snapshot, _ = operator_snapshot()
    generate_matches(snapshot)
    repo = Repository(tmp_path / "event")
    with repo.lock():
        repo.save(snapshot, None)
    service = OperatorService(repo)
    try:
        confirm(service, preview(service, action="schedule"))
        yield service
    finally:
        service.close()


def update(snapshot, assignment, *, delta=timedelta(), court=None):
    zone = ZoneInfo(snapshot.config["event"]["timezone"])
    return {
        "match_id": assignment.match_id,
        "local_start": (assignment.start_at + delta).astimezone(zone).strftime("%Y-%m-%dT%H:%M"),
        "court_id": court or assignment.court_id,
    }


def test_batch_preview_before_after_and_persistent_review(scheduled):
    original = scheduled.repo.load()
    public = scheduled.public_snapshot()
    updates = [update(original, a, delta=timedelta(minutes=30)) for a in original.assignments]
    plan = preview(scheduled, action="manual_schedule", updates=updates)
    review = plan["summary"]["schedule_review"]
    assert plan["summary"]["can_apply"] and review["can_apply"]
    assert review["changed_count"] == len(updates)
    assert all("time" in change["changed_fields"] for change in review["changes"])
    assert content_hash(scheduled.repo.load()) == content_hash(original)
    assert scheduled.public_snapshot() == public
    saved = confirm(scheduled, plan)
    assert saved["summary"]["schedule_review"] == review
    assert scheduled.public_snapshot()["version"] != public["version"]
    restarted = OperatorService(scheduled.repo)
    try:
        record = restarted.state()["schedule_reviews"][0]
        assert record["schedule_review"] == review
        assert record["result_revision_id"] == saved["revision_id"]
        assert "schedule_review" not in restarted.public_snapshot()
    finally:
        restarted.close()


def test_invalid_draft_shows_new_conflicts_but_confirm_is_rejected(scheduled):
    original = scheduled.repo.load()
    a, b = sorted(original.assignments, key=lambda a: a.start_at)[:2]
    proposal = update(original, a, court=b.court_id)
    proposal["local_start"] = update(original, b)["local_start"]
    plan = preview(scheduled, action="manual_schedule", updates=[proposal])
    summary = plan["summary"]
    assert not summary["can_apply"]
    assert any(
        i["code"] == "COURT_OVERLAP" for i in summary["schedule_review"]["conflicts"]["introduced"]
    )
    with pytest.raises(Conflict, match="衝突"):
        confirm(scheduled, plan)
    assert content_hash(scheduled.repo.load()) == content_hash(original)


def test_conflict_repair_distinguishes_resolved_from_remaining(scheduled):
    original = scheduled.repo.load()
    a, b = sorted(original.assignments, key=lambda a: a.start_at)[:2]
    broken = original.model_copy(deep=True)
    collision = b.model_copy(update={"match_id": a.match_id})
    broken.assignments = [collision if x.match_id == a.match_id else x for x in broken.assignments]
    with scheduled.repo.lock():
        scheduled.repo.save(broken, original.id)
    plan = preview(scheduled, action="manual_schedule", updates=[update(original, a)])
    report = plan["summary"]["schedule_review"]
    assert report["conflicts"]["resolved"]
    assert not report["conflicts"]["introduced"]
    assert not report["conflicts"]["remaining"]
    assert plan["summary"]["can_apply"]
    confirm(scheduled, plan)


@pytest.mark.parametrize("protected", ["time", "court", "in_progress", "completed"])
def test_locked_and_started_matches_are_not_writable(scheduled, protected):
    original = scheduled.repo.load()
    a = original.assignments[0]
    if protected in {"time", "court"}:
        a.locks[protected] = True
    else:
        next(m for m in original.matches if m.id == a.match_id).status = protected
    with scheduled.repo.lock():
        original = scheduled.repo.save(original, original.id)
    court = next(c["id"] for c in original.config["courts"] if c["id"] != a.court_id)
    plan = preview(
        scheduled,
        action="manual_schedule",
        updates=[update(original, a, delta=timedelta(minutes=30), court=court)],
    )
    assert not plan["summary"]["can_apply"]
    assert any(
        i["code"] == "LOCK_VIOLATION"
        for i in plan["summary"]["schedule_review"]["conflicts"]["introduced"]
    )
    with pytest.raises(Conflict):
        confirm(scheduled, plan)
    assert content_hash(scheduled.repo.load()) == content_hash(original)


def test_new_revision_invalidates_manual_draft(scheduled):
    original = scheduled.repo.load()
    updates = [update(original, a, delta=timedelta(minutes=30)) for a in original.assignments]
    one = preview(scheduled, action="manual_schedule", updates=updates)
    two = preview(scheduled, action="manual_schedule", updates=updates)
    confirm(scheduled, two)
    with pytest.raises(Conflict, match="更新"):
        confirm(scheduled, one)


def test_noop_duplicate_and_outside_event_date_cannot_be_applied(scheduled):
    original = scheduled.repo.load()
    a = original.assignments[0]
    plan = preview(scheduled, action="manual_schedule", updates=[update(original, a)])
    assert not plan["summary"]["can_apply"]
    with pytest.raises(ValueError, match="重複"):
        preview(
            scheduled, action="manual_schedule", updates=[update(original, a), update(original, a)]
        )
    outside = preview(
        scheduled, action="manual_schedule", updates=[update(original, a, delta=timedelta(days=30))]
    )
    assert not outside["summary"]["can_apply"]
    with pytest.raises(Conflict):
        confirm(scheduled, outside)


def test_manual_changes_still_enforce_confirmed_sixty_minute_policy(scheduled):
    from squash_scheduler.scheduling import solve_schedule
    from squash_scheduler.scheduling.constraints import build_context

    s = scheduled.repo.load()
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    assert solve_schedule(s, time_limit_seconds=5)["status"] in {"FEASIBLE", "OPTIMAL"}
    with scheduled.repo.lock():
        s = scheduled.repo.save(s, s.id)
    context, _ = build_context(s)
    ordered = sorted(s.assignments, key=lambda a: a.start_at)
    first, second = next(
        (a, b)
        for i, a in enumerate(ordered)
        for b in ordered[i + 1 :]
        if context.shared_people(a.match_id, b.match_id)
    )
    u = update(s, second)
    u["local_start"] = (
        (first.play_end_at + timedelta(minutes=15))
        .astimezone(ZoneInfo(s.config["event"]["timezone"]))
        .strftime("%Y-%m-%dT%H:%M")
    )
    plan = preview(scheduled, action="manual_schedule", updates=[u])
    assert not plan["summary"]["can_apply"]
    assert any(
        i["code"] == "PLAYER_REST_VIOLATION"
        for i in plan["summary"]["schedule_review"]["conflicts"]["introduced"]
    )
    assert plan["summary"]["schedule_review"]["policy"]["minimum_rest_minutes"] == 60


def test_date_change_uses_event_timezone_and_preserves_clock_time(scheduled):
    s = scheduled.repo.load()
    day = s.config["event"]["event_dates"][0]
    from datetime import date

    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    s.config["event"]["event_dates"].append(next_day)
    anchor = s.config["timing_defaults"]["daily_grid_anchors"][0]
    s.config["timing_defaults"]["daily_grid_anchors"].append({**anchor, "date": next_day})
    from datetime import datetime

    for court in s.config["courts"]:
        court["availability"].extend(
            [
                {
                    key: (datetime.fromisoformat(window[key]) + timedelta(days=1)).isoformat()
                    for key in ("start_at", "end_at")
                }
                for window in court["availability"][:]
            ]
        )
    from squash_scheduler.domain.matches import generate_matches

    generate_matches(s)
    with scheduled.repo.lock():
        s = scheduled.repo.save(s, s.id)
    plan = preview(
        scheduled,
        action="manual_schedule",
        updates=[update(s, a, delta=timedelta(days=1)) for a in s.assignments],
    )
    report = plan["summary"]["schedule_review"]
    assert report["can_apply"], [
        (e["code"], e["detail"])
        for e in report["conflicts"]["introduced"] + report["conflicts"]["remaining"]
    ]
    assert all(
        c["changed_fields"] == ["date"]
        and c["before"]["time"] == c["after"]["time"]
        and c["after"]["date"] == next_day
        for c in report["changes"]
    )
