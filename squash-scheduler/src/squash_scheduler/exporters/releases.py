"""Finalize a reviewed local bundle without changing its build or any prior release."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import UUID

from ..domain.models import canonical_hash, content_hash, new_id, utcnow
from ..domain.revisions import ReviewRequired
from ..storage.repository import Repository, RepositoryError, atomic_json

VISUAL_CHECKS = {"all_pages", "glyphs", "no_clipping", "no_overlap", "black_and_white_readable"}


def finalize_release(repo: Repository, review: dict, actor: str) -> dict:
    """Caller holds tournament lock. A review certifies the exact rendered pages."""
    release_id = review["release_id"]
    UUID(release_id)
    original = repo.root / "releases" / release_id
    manifest = json.loads((original / "manifest.json").read_text("utf-8"))
    if manifest.get("status") != "qa_pending" or canonical_hash(manifest) != review.get(
        "manifest_hash"
    ):
        raise RepositoryError("Release QA basis is stale or release is not awaiting review")
    if (
        not actor.strip()
        or review.get("actor_id") != actor
        or not str(review.get("reason", "")).strip()
    ):
        raise ReviewRequired("Release review requires matching actor and reason")
    expected_pages = [p["page"] for p in manifest["qa"]["pages"]]
    if sorted(review.get("reviewed_pages", [])) != expected_pages:
        raise ReviewRequired("Every rendered page must be reviewed exactly once")
    checks = review.get("checks", {})
    if any(checks.get(name) is not True for name in VISUAL_CHECKS):
        raise ReviewRequired("All required visual checks must explicitly pass")
    snapshot = repo.load(manifest["revision_id"])
    if snapshot.status != "approved" or content_hash(snapshot) != manifest["content_hash"]:
        raise RepositoryError("Release does not match an immutable approved revision")
    for filename, digest in manifest["artifact_hashes"].items():
        if Path(filename).name != filename or filename in (".", ".."):
            raise RepositoryError("Invalid artifact filename")
        path = original / filename
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise RepositoryError("Release artifact checksum mismatch")
    ready_id = new_id()
    pending = original.parent / (".pending-" + ready_id)
    pending.mkdir()
    try:
        for filename in manifest["artifact_hashes"]:
            shutil.copyfile(original / filename, pending / filename)
        approval = {**review, "id": new_id(), "created_at": utcnow().isoformat()}
        atomic_json(pending / "visual-review.json", approval)
        manifest.update(
            release_id=ready_id,
            parent_release_id=release_id,
            status="ready",
            ready_at=utcnow().isoformat(),
            visual_review=approval,
        )
        manifest["artifact_hashes"]["visual-review.json"] = hashlib.sha256(
            (pending / "visual-review.json").read_bytes()
        ).hexdigest()
        atomic_json(pending / "manifest.json", manifest)
        final = original.parent / ready_id
        pending.rename(final)
        atomic_json(
            original.parent / "latest-ready.json",
            {
                "release_id": ready_id,
                "revision_id": snapshot.id,
                "manifest_hash": canonical_hash(manifest),
            },
        )
        return {
            "release_id": ready_id,
            "status": "ready",
            "path": str(final),
            "revision_id": snapshot.id,
        }
    finally:
        if pending.exists():
            shutil.rmtree(pending)
