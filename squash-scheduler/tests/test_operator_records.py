"""Immutable recovery and untrusted backup/export boundary coverage."""

import hashlib
import json
import stat
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_formats_draws import event

from squash_scheduler.domain.models import Assignment, EntrySide, Match, Stage, new_id
from squash_scheduler.operator_records import (
    create_backup,
    execute_restore_revision,
    history,
    inspect_backup,
    load_jobs,
    preview_restore_revision,
    restore_backup,
    save_job,
)
from squash_scheduler.storage.repository import Repository, RepositoryError, atomic_json


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "event")
    snapshot, _ = event(4)
    snapshot.config["event"] = {"name": "虛構測試賽"}
    with repository.lock():
        repository.save(snapshot, None)
    return repository


def revision(repo, reason="更改測試設定"):
    snapshot = repo.load()
    snapshot.config["test"] = True
    snapshot.changes.append(
        {
            "id": new_id(),
            "kind": "operator_action",
            "actor_id": "Justin",
            "reason": reason,
            "operation": "seeds",
        }
    )
    with repo.lock():
        return repo.save(snapshot, snapshot.id)


def test_history_and_restore_append_revision_without_changing_old_files(repo):
    original = repo.load()
    old_file = repo.root / "revisions" / original.id / "snapshot.json"
    old_bytes = old_file.read_bytes()
    current = revision(repo)
    rows = history(repo)
    assert rows[0]["actor"] == "Justin"
    assert rows[0]["diff"][0]["label"].startswith("賽事設定")
    assert rows[1]["restorable"]
    proposal = preview_restore_revision(repo, current, original.id, "Justin", "撤銷測試設定")
    assert proposal["diff"]
    working = execute_restore_revision(repo, current, proposal)
    assert "test" not in working.config
    assert working.status == "draft"
    assert not any(c.get("operation") == "seeds" for c in working.changes)
    assert working.changes[-1]["source_revision_id"] == current.id
    with repo.lock():
        saved = repo.save(working, current.id)
    assert saved.parent_id == current.id
    assert old_file.read_bytes() == old_bytes
    assert repo.load(current.id).changes[-1]["operation"] == "seeds"


def test_restore_checks_full_snapshot_even_when_content_hash_is_unchanged(repo):
    original = repo.load()
    current = revision(repo)
    proposal = preview_restore_revision(repo, current, original.id, "Justin", "復原")
    current.changes.append({"id": new_id(), "kind": "entry_change"})
    with pytest.raises(RepositoryError, match="更新"):
        execute_restore_revision(repo, current, proposal)


def test_history_focuses_on_changed_player_instead_of_unchanged_json_prefix(repo):
    before = repo.load()
    for person in before.participants:
        person.display_name = "未異動資料" * 50
    before.participants[-1].display_name = "王小明"
    with repo.lock():
        before = repo.save(before, before.id)
    after = before.model_copy(deep=True)
    after.participants[-1].display_name = "王大明"
    after.entries[-1].status = "withdrawn"
    with repo.lock():
        repo.save(after, before.id)
    diffs = history(repo)[0]["diff"]
    player = next(row for row in diffs if row["path"] == "participants")
    entry = next(row for row in diffs if row["path"] == "entries")
    assert player["before"] == "顯示姓名：王小明"
    assert player["after"] == "顯示姓名：王大明"
    assert "未異動資料" not in player["before"]
    assert "王大明" in entry["label"]
    assert "有效參賽" in entry["before"] and "已退賽" in entry["after"]
    assert before.entries[-1].id not in str(diffs)


def test_history_assignments_show_match_court_and_event_local_time(repo):
    original = repo.load()
    stage = Stage(division_id=original.divisions[0].id, type="elimination")
    match = Match(
        division_id=original.divisions[0].id,
        stage_id=stage.id,
        side_a=EntrySide(entry_id=original.entries[0].id),
        side_b=EntrySide(entry_id=original.entries[1].id),
        display_code="DEMO-001",
    )
    original.stages.append(stage)
    original.matches.append(match)
    court = new_id()
    original.config["courts"] = [{"id": court, "label": "場地A"}]
    start = datetime(2030, 1, 1, 1, tzinfo=UTC)
    original.assignments.append(
        Assignment(
            match_id=match.id,
            court_id=court,
            start_at=start,
            play_end_at=start + timedelta(minutes=25),
            court_release_at=start + timedelta(minutes=30),
        )
    )
    with repo.lock():
        before = repo.save(original, original.id)
    after = before.model_copy(deep=True)
    assignment = after.assignments[0]
    # Add to all timestamps in one validated replacement.
    after.assignments[0] = Assignment(
        match_id=match.id,
        court_id=court,
        start_at=assignment.start_at + timedelta(minutes=15),
        play_end_at=assignment.play_end_at + timedelta(minutes=15),
        court_release_at=assignment.court_release_at + timedelta(minutes=15),
    )
    with repo.lock():
        repo.save(after, before.id)
    row = next(r for r in history(repo)[0]["diff"] if r["path"] == "assignments")
    assert "DEMO-001" in row["label"]
    assert "2030-01-01 09:00" in row["before"]
    assert "2030-01-01 09:15" in row["after"]
    assert match.id not in row["before"] and court not in row["after"]


def test_restore_preview_exposes_target_actor_reason_and_actual_changes(repo):
    original = revision(repo, reason="第一版已核對")
    current = original.model_copy(deep=True)
    current.entries[-1].status = "withdrawn"
    with repo.lock():
        current = repo.save(current, original.id)
    proposal = preview_restore_revision(repo, current, original.id, "Justin", "復原測試")
    assert proposal["target_actor"] == "Justin"
    assert proposal["target_reason"] == "第一版已核對"
    assert any("已退賽" in row["before"] and "有效參賽" in row["after"] for row in proposal["diff"])


@pytest.mark.parametrize("side", ["current", "target"])
def test_restore_refuses_result_on_either_side(repo, side):
    original = repo.load()
    if side == "target":
        original.results = [{"match_id": new_id()}]
        with repo.lock():
            original = repo.save(original, original.id)
    current = revision(repo)
    if side == "current":
        current.results = [{"match_id": new_id()}]
    else:
        current.results = []
    with pytest.raises(ValueError, match="開賽或成績"):
        preview_restore_revision(repo, current, original.id, "Justin", "復原")


@pytest.mark.parametrize("blocking", ["in_progress", "completed", "locked"])
def test_restore_refuses_live_or_locked_matches(repo, blocking):
    original = repo.load()
    current = revision(repo)
    stage = Stage(
        division_id=current.divisions[0].id, stage_key="main", label="Main", type="elimination"
    )
    match = Match(
        division_id=current.divisions[0].id,
        stage_id=stage.id,
        side_a=EntrySide(entry_id=current.entries[0].id),
        side_b=EntrySide(entry_id=current.entries[1].id),
    )
    current.stages.append(stage)
    current.matches.append(match)
    if blocking == "locked":
        start = datetime(2030, 1, 1, 9, tzinfo=UTC)
        current.assignments.append(
            Assignment(
                match_id=match.id,
                court_id=new_id(),
                start_at=start,
                play_end_at=start + timedelta(minutes=25),
                court_release_at=start + timedelta(minutes=30),
                locks={"time": True},
            )
        )
    else:
        match.status = blocking
    with pytest.raises(ValueError):
        preview_restore_revision(repo, current, original.id, "Justin", "復原")


def test_backup_roundtrip_preserves_history_sources_assets_and_current_workspace(repo):
    original = repo.load()
    revision(repo)
    for name, raw in [
        ("assets/font.otf", b"controlled-font"),
        ("source/raw/original", b"original-document"),
        ("draws/test.json", b"{}"),
        ("operator-exports/large.pdf", b"not-included"),
    ]:
        path = repo.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    before = (repo.root / "working/current.json").read_bytes()
    with repo.lock():
        archive = create_backup(repo)
        metadata = inspect_backup(archive)
        restored = restore_backup(repo, archive, "Justin", "還原演練")
    assert metadata["revision_count"] == 2
    assert metadata["event_name"] == "虛構測試賽"
    target = Path(restored["workspace"])
    assert target != repo.root
    assert (target / "assets/font.otf").read_bytes() == b"controlled-font"
    assert (target / "source/raw/original").read_bytes() == b"original-document"
    assert not (target / "operator-exports").exists()
    assert Repository(target).load(original.id).id == original.id
    assert (repo.root / "working/current.json").read_bytes() == before
    assert (repo.root / "operator-restores" / (restored["restore_id"] + ".json")).is_file()
    assert not (target / ".write.lock").exists()


def test_backup_does_not_recurse_into_existing_backup_or_restored_workspace(repo):
    with repo.lock():
        first = create_backup(repo)
        restore_backup(repo, first, "Justin", "演練")
        second = create_backup(repo)
    with zipfile.ZipFile(second) as archive:
        assert not any(
            name.startswith(("operator-backups/", "restored-workspaces/", "operator-restores/"))
            for name in archive.namelist()
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape.json",
        "working/../../escape.json",
        "C:/escape.json",
        "working\\escape.json",
        "/working/current.json",
    ],
)
def test_backup_rejects_path_traversal(repo, unsafe):
    archive = repo.root / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(unsafe, b"bad")
    with pytest.raises(ValueError):
        inspect_backup(archive)


def test_backup_rejects_tampered_file_even_with_valid_zip(repo):
    with repo.lock():
        original = create_backup(repo)
    changed = repo.root / "tampered.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            target.writestr(
                name, b"tamper" if name == "working/current.json" else source.read(name)
            )
    with pytest.raises(ValueError, match="校驗"):
        inspect_backup(changed)


def test_backup_rejects_duplicate_names_and_invalid_zip(repo):
    archive = repo.root / "bad.zip"
    archive.write_bytes(b"not-a-zip")
    with pytest.raises(ValueError, match="格式"):
        inspect_backup(archive)
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("working/current.json", b"a")
        handle.writestr("WORKING/current.json", b"b")
    with pytest.raises(ValueError, match="重複"):
        inspect_backup(archive)


def test_backup_rejects_zip_symlink_and_high_expansion(repo, monkeypatch):
    archive = repo.root / "bad.zip"
    link = zipfile.ZipInfo("working/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(link, "../outside")
    with pytest.raises(ValueError, match="檔案類型"):
        inspect_backup(archive)
    monkeypatch.setattr("squash_scheduler.operator_records._MAX_BYTES", 100)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("working/payload.json", b"x" * 1000)
    with pytest.raises(ValueError, match="大小超過"):
        inspect_backup(archive)


def test_restored_backup_retains_export_history_without_download_link(repo):
    job, _ = export_job(repo)
    with repo.lock():
        archive = create_backup(repo)
        restored = restore_backup(repo, archive, "Justin", "還原演練")
    target = Repository(Path(restored["workspace"]))
    recovered = load_jobs(target)[job["id"]]
    assert recovered["status"] == "failed"
    assert "url" not in recovered
    assert "備份不含匯出文件" in recovered["error"]


def export_job(repo, *, receipt=True):
    identifier = new_id()
    directory = repo.root / "operator-exports" / identifier
    directory.mkdir(parents=True)
    (directory / "index.html").write_bytes(b"<p>verified</p>")
    manifest = {
        "revision_id": repo.load().id,
        "created_at": "2030-01-01T00:00:00+00:00",
        "qa": {"passed": True},
        "artifact_hashes": {"index.html": hashlib.sha256(b"<p>verified</p>").hexdigest()},
    }
    atomic_json(directory / "manifest.json", manifest)
    job = {
        "id": identifier,
        "status": "completed",
        "directory": str(directory),
        "revision_id": repo.load().id,
    }
    if receipt:
        save_job(repo, job)
    return job, directory


def test_export_history_survives_restart_and_recovers_old_verified_export(repo):
    job, _ = export_job(repo)
    legacy, _ = export_job(repo, receipt=False)
    recovered = load_jobs(repo)
    assert recovered[job["id"]]["status"] == "completed"
    assert recovered[legacy["id"]]["url"].endswith("/index.html")
    raw = json.loads((repo.root / "operator-jobs" / (job["id"] + ".json")).read_text("utf-8"))
    assert "directory" not in raw


def test_corrupted_export_cannot_be_recovered_as_completed(repo):
    job, directory = export_job(repo)
    (directory / "index.html").write_bytes(b"corrupted")
    recovered = load_jobs(repo)[job["id"]]
    assert recovered["status"] == "failed"
    assert "url" not in recovered


def test_interrupted_job_becomes_failed_and_ignores_persisted_absolute_path(repo):
    identifier = new_id()
    save_job(repo, {"id": identifier, "status": "running", "directory": "C:/secret"})
    recovered = load_jobs(repo)[identifier]
    assert recovered["status"] == "failed"
    assert "中斷" in recovered["error"]
    assert Path(recovered["directory"]) == repo.root / "operator-exports" / identifier
    assert "url" not in recovered


def test_export_manifest_cannot_reference_external_files(repo):
    job, directory = export_job(repo)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["artifact_hashes"]["../secret"] = "irrelevant"
    atomic_json(manifest_path, manifest)
    recovered = load_jobs(repo)[job["id"]]
    assert recovered["status"] == "failed"
    assert "url" not in recovered
