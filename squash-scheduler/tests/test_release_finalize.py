"""Only the exact reviewed complete bundle can become the newest ready release."""

import hashlib
import json

import pytest

from squash_scheduler.domain.models import Snapshot, canonical_hash, content_hash, new_id
from squash_scheduler.domain.revisions import ReviewRequired
from squash_scheduler.exporters.releases import VISUAL_CHECKS, finalize_release
from squash_scheduler.storage.repository import Repository, RepositoryError, atomic_json


def pending_bundle(tmp_path):
    repo = Repository(tmp_path / "event")
    with repo.lock():
        snapshot = repo.save(Snapshot(config={}, status="approved"), None)
    release_id = new_id()
    folder = repo.root / "releases" / release_id
    folder.mkdir(parents=True)
    (folder / "schedule.xlsx").write_bytes(b"synthetic complete workbook")
    (folder / "booklet.pdf").write_bytes(b"synthetic complete pdf")
    manifest = {
        "status": "qa_pending",
        "revision_id": snapshot.id,
        "content_hash": content_hash(snapshot),
        "artifact_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()
        },
        "qa": {"pages": [{"page": 1}, {"page": 2}]},
    }
    atomic_json(folder / "manifest.json", manifest)
    review = {
        "release_id": release_id,
        "manifest_hash": canonical_hash(manifest),
        "actor_id": "synthetic-reviewer",
        "reason": "Test-only page review",
        "reviewed_pages": [1, 2],
        "checks": dict.fromkeys(VISUAL_CHECKS, True),
    }
    return repo, folder, review


def test_finalized_bundle_preserves_original_and_sets_atomic_ready_pointer(tmp_path):
    repo, folder, review = pending_bundle(tmp_path)
    original = (folder / "manifest.json").read_bytes()
    with repo.lock():
        result = finalize_release(repo, review, "synthetic-reviewer")
    assert result["status"] == "ready" and result["release_id"] != folder.name
    assert (folder / "manifest.json").read_bytes() == original
    ready = folder.parent / result["release_id"]
    manifest = json.loads((ready / "manifest.json").read_text("utf-8"))
    assert manifest["parent_release_id"] == folder.name
    for filename, digest in manifest["artifact_hashes"].items():
        assert hashlib.sha256((ready / filename).read_bytes()).hexdigest() == digest
    pointer = json.loads((folder.parent / "latest-ready.json").read_text("utf-8"))
    assert pointer["release_id"] == result["release_id"]
    assert pointer["manifest_hash"] == canonical_hash(manifest)


@pytest.mark.parametrize(
    "change", ["page_missing", "page_duplicate", "unchecked", "stale", "actor", "tampered"]
)
def test_incomplete_or_stale_review_never_changes_latest_ready(tmp_path, change):
    repo, folder, review = pending_bundle(tmp_path)
    latest = folder.parent / "latest-ready.json"
    atomic_json(latest, {"release_id": "old-ready"})
    before = latest.read_bytes()
    if change == "page_missing":
        review["reviewed_pages"] = [1]
    elif change == "page_duplicate":
        review["reviewed_pages"] = [1, 1, 2]
    elif change == "unchecked":
        review["checks"]["glyphs"] = False
    elif change == "stale":
        review["manifest_hash"] = "old-hash"
    elif change == "actor":
        review["actor_id"] = "wrong-reviewer"
    else:
        (folder / "booklet.pdf").write_bytes(b"changed PDF")
    with pytest.raises((RepositoryError, ReviewRequired)), repo.lock():
        finalize_release(repo, review, "synthetic-reviewer")
    assert latest.read_bytes() == before
    assert not list(folder.parent.glob(".pending-*"))
