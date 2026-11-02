# ruff: noqa: F811
"""Backup review, isolated restore and workspace switches through the operator boundary."""

import hashlib

import pytest
from test_operator import confirm, preview, service  # noqa: F401 - shared repository fixture

from squash_scheduler.operations import Conflict


def metadata(service):
    return {"revision_id": service.repo.load().id, "actor": "Justin", "reason": "演練備份與還原"}


def test_live_start_confirmation_rechecks_exact_time_and_keeps_approved_assignment(
    service, monkeypatch
):
    from datetime import timedelta

    from squash_scheduler import operator_actions
    from squash_scheduler.domain.matches import generate_matches
    from squash_scheduler.scheduling import solve_schedule

    snapshot = service.repo.load()
    generate_matches(snapshot)
    assert solve_schedule(snapshot, time_limit_seconds=3)["status"] in {"FEASIBLE", "OPTIMAL"}
    with service.repo.lock():
        snapshot = service.repo.save(snapshot, snapshot.id)
    first = min(snapshot.assignments, key=lambda a: a.start_at)
    monkeypatch.setattr(operator_actions, "utcnow", lambda: first.start_at)
    plan = preview(
        service, action="live_match", event="start", match_id=first.match_id, delay_minutes=0
    )
    assert service.repo.load().id == snapshot.id
    monkeypatch.setattr(operator_actions, "utcnow", lambda: first.start_at + timedelta(minutes=3))
    with pytest.raises(ValueError, match="兩分鐘"):
        confirm(service, plan)
    assert service.repo.load().id == snapshot.id
    monkeypatch.setattr(operator_actions, "utcnow", lambda: first.start_at)
    confirm(
        service,
        preview(
            service, action="live_match", event="start", match_id=first.match_id, delay_minutes=0
        ),
    )
    after = service.repo.load()
    assert next(m for m in after.matches if m.id == first.match_id).status == "in_progress"
    assert after.assignments == snapshot.assignments
    assert not service.state()["history"][1]["restorable"]


def test_backup_restore_is_independent_and_switch_invalidates_every_preview(service):
    original = service.repo
    before = original.load()
    saved = service.backup(metadata(service))
    archive = service.backup_artifact(saved["id"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == saved["archive_sha256"]
    assert service.state()["backups"][0]["id"] == saved["id"]
    plan = service.restore_preview(archive.read_bytes(), metadata(service))
    restored = service.restore_confirm({"id": plan["id"], "hash": plan["hash"]})
    assert original.load().id == before.id
    assert service.repo.root == original.root
    assert restored["restore_id"] in {w["id"] for w in service.workspaces()}
    seed_plan = preview(
        service, action="seeds", division_id=before.divisions[0].id, mode="none", seeds=[]
    )
    upload_plan = service.restore_preview(archive.read_bytes(), metadata(service))
    service.switch_workspace({**metadata(service), "restore_id": restored["restore_id"]})
    assert service.repo.root != original.root
    with pytest.raises(Conflict):
        confirm(service, seed_plan)
    with pytest.raises(Conflict):
        service.restore_confirm({"id": upload_plan["id"], "hash": upload_plan["hash"]})
    service.switch_workspace({**metadata(service), "restore_id": "original"})
    assert service.repo.root == original.root
    assert original.load().id == before.id


def test_restore_retains_target_calls_and_delays_but_discards_later_events(service, monkeypatch):
    from squash_scheduler import operator_actions
    from squash_scheduler.domain.matches import generate_matches
    from squash_scheduler.scheduling import solve_schedule

    initial = service.repo.load()
    generate_matches(initial)
    assert solve_schedule(initial, time_limit_seconds=3)["status"] in {"FEASIBLE", "OPTIMAL"}
    with service.repo.lock():
        initial = service.repo.save(initial, initial.id)
    first = min(initial.assignments, key=lambda a: a.start_at)
    monkeypatch.setattr(operator_actions, "utcnow", lambda: first.start_at)
    confirm(
        service,
        preview(
            service, action="live_match", event="call", match_id=first.match_id, delay_minutes=0
        ),
    )
    confirm(
        service,
        preview(
            service, action="live_match", event="delay", match_id=first.match_id, delay_minutes=15
        ),
    )
    target = service.repo.load()
    expected_board = operator_actions.state_fields(target)["live_board"]
    expected_options = operator_actions.schedule_live_options(target)
    confirm(
        service,
        preview(
            service,
            action="live_match",
            event="clear_delay",
            match_id=first.match_id,
            delay_minutes=0,
        ),
    )
    confirm(service, preview(service, action="restore_revision", target_revision_id=target.id))
    restored = service.repo.load()
    assert operator_actions.state_fields(restored)["live_board"] == expected_board
    assert operator_actions.schedule_live_options(restored) == expected_options
    rows = [row for court in expected_board["courts"] for row in court["matches"]]
    delayed = next(row for row in rows if row["match_id"] == first.match_id)
    assert delayed["called_at"] and delayed["delay_active"]
    replan = preview(service, action="local_replan", change_id=delayed["change_id"])
    confirm(service, replan)
    confirm(service, preview(service, action="restore_revision", target_revision_id=initial.id))
    assert operator_actions.schedule_live_options(service.repo.load()) == {}
    confirm(service, preview(service, action="restore_revision", target_revision_id=target.id))
    assert operator_actions.state_fields(service.repo.load())["live_board"] == expected_board


def test_restore_confirmation_checks_version_and_archive_digest(service):
    saved = service.backup(metadata(service))
    archive = service.backup_artifact(saved["id"])
    plan = service.restore_preview(archive.read_bytes(), metadata(service))
    internal = service.restore_plans[plan["id"]]
    from pathlib import Path

    Path(internal["archive"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="變更"):
        service.restore_confirm({"id": plan["id"], "hash": plan["hash"]})
    assert not list((service.base_repo.root / "restored-workspaces").glob("*"))
    plan = service.restore_preview(archive.read_bytes(), metadata(service))
    snapshot = service.repo.load()
    confirm(
        service,
        preview(
            service, action="seeds", division_id=snapshot.divisions[0].id, mode="none", seeds=[]
        ),
    )
    with pytest.raises(Conflict):
        service.restore_confirm({"id": plan["id"], "hash": plan["hash"]})


def test_switch_logging_failure_preserves_current_workspace_and_plans(service, monkeypatch):
    from squash_scheduler import operations

    saved = service.backup(metadata(service))
    plan = service.restore_preview(
        service.backup_artifact(saved["id"]).read_bytes(), metadata(service)
    )
    restored = service.restore_confirm({"id": plan["id"], "hash": plan["hash"]})
    before = service.repo.root
    seed_plan = preview(
        service,
        action="seeds",
        division_id=service.repo.load().divisions[0].id,
        mode="none",
        seeds=[],
    )

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(operations, "atomic_json", fail)
    with pytest.raises(OSError):
        service.switch_workspace({**metadata(service), "restore_id": restored["restore_id"]})
    assert service.repo.root == before
    assert seed_plan["id"] in service.plans


def test_backup_download_rejects_modified_archive_and_bad_workspace_ids(service):
    saved = service.backup(metadata(service))
    service.backup_artifact(saved["id"]).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        service.backup_artifact(saved["id"])
    with pytest.raises(ValueError):
        service.switch_workspace({**metadata(service), "restore_id": "../../other"})
