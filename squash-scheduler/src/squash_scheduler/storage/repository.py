"""Immutable revisions, optimistic concurrency and one writer per tournament."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from ..domain.models import Snapshot, canonical_hash, canonical_json, content_hash, new_id, utcnow


class RepositoryError(ValueError):
    code = "REVISION_CONFLICT"


def _replace_with_retry(source: str | Path, destination: Path) -> None:
    """Windows scanners can briefly retain a closed file; keep the rename atomic."""
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def _rename_new(source: Path, destination: Path) -> None:
    for attempt in range(6):
        if destination.exists():
            raise RepositoryError("Immutable revision directory already exists")
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(data))
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


class Repository:
    def __init__(self, workspace: Path):
        self.root = workspace.resolve()

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lockfile = self.root / ".write.lock"
        try:
            fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RepositoryError("Tournament is locked by another writer") from exc
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            yield
        finally:
            lockfile.unlink(missing_ok=True)

    def load(self, revision_id: str | None = None) -> Snapshot:
        if revision_id:
            UUID(revision_id)
            path = self.root / "revisions" / revision_id / "snapshot.json"
        else:
            path = self.root / "working" / "current.json"
        envelope = json.loads(path.read_text("utf-8"))
        snapshot = Snapshot.model_validate(envelope["snapshot"])
        if (
            content_hash(snapshot) != envelope["content_hash"]
            or canonical_hash(envelope["snapshot"]) != envelope["snapshot_hash"]
        ):
            raise RepositoryError("Snapshot checksum mismatch")
        return snapshot

    def save(
        self,
        snapshot: Snapshot,
        base_revision_id: str | None,
        *,
        report: dict | None = None,
        approval: dict | None = None,
        revision_id: str | None = None,
    ) -> Snapshot:
        """Call while holding lock; revisions become visible only after fully written."""
        current = self.root / "working" / "current.json"
        if current.exists():
            old = self.load()
            if old.id != base_revision_id:
                raise RepositoryError("Base revision is stale")
        elif base_revision_id is not None:
            raise RepositoryError("Base revision does not exist")
        snapshot = snapshot.model_copy(deep=True)
        if revision_id is not None:
            UUID(revision_id)
            if (self.root / "revisions" / revision_id).exists():
                raise RepositoryError("Revision ID already exists")
        snapshot.id = revision_id or new_id()
        snapshot.parent_id = base_revision_id
        snapshot.created_at = utcnow()
        data = snapshot.model_dump(mode="json")
        envelope = {
            "schema_version": "1.0.0",
            "content_hash": content_hash(snapshot),
            "snapshot_hash": canonical_hash(data),
            "snapshot": data,
        }
        revisions = self.root / "revisions"
        revisions.mkdir(parents=True, exist_ok=True)
        pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=revisions))
        try:
            atomic_json(pending / "snapshot.json", envelope)
            if report is not None:
                atomic_json(pending / "validation-report.json", report)
            atomic_json(pending / "review-actions.json", approval or snapshot.approvals)
            _rename_new(pending, revisions / snapshot.id)
            atomic_json(current, envelope)
        finally:
            if pending.exists():
                shutil.rmtree(pending)
        return snapshot

    def archive_source(self, path: Path, metadata: dict) -> None:
        source_id = metadata["id"]
        UUID(source_id)
        folder = self.root / "source" / source_id
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise RepositoryError("Source changed between parsing and archival")
        if folder.exists():
            if hashlib.sha256((folder / "original").read_bytes()).hexdigest() != metadata["sha256"]:
                raise RepositoryError("Archived source checksum mismatch")
            return
        folder.mkdir(parents=True)
        try:
            with (folder / "original").open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            atomic_json(folder / "metadata.json", metadata)
        except Exception:
            shutil.rmtree(folder)
            raise
