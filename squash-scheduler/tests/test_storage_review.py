"""Persistence and approval regressions using the real event configuration contract."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from squash_scheduler.domain.config import EventConfig, load_config
from squash_scheduler.domain.models import (
    Division,
    Entry,
    Participant,
    Registration,
    Snapshot,
    content_hash,
    new_id,
)
from squash_scheduler.domain.revisions import ReviewRequired, apply_patch, approve
from squash_scheduler.domain.validation import approval_valid, target_basis, validate_references
from squash_scheduler.storage import repository as storage
from squash_scheduler.storage.repository import Repository, RepositoryError, atomic_json


def template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "squash-event-config.template.json"


@pytest.fixture
def config():
    return load_config(template_path())


@pytest.fixture
def snapshot(config):
    division = Division(name="Synthetic division", level_order=0)
    division.format_plan = {
        "template_id": "round_robin",
        "template_version": "1.0.0",
        "status": "draft",
        "parameters": {},
    }
    config["divisions"] = [division.model_dump(mode="json")]
    config["divisions_status"] = "configured"
    return Snapshot(config=config, divisions=[division])


def patch_for(snapshot, operations):
    return {
        "base_revision_id": snapshot.id,
        "actor_id": "synthetic-organizer",
        "reason": "Reviewed test amendment",
        "operations": operations,
    }


def publication_operation(snapshot, url="https://example.org/synthetic-event"):
    return {
        "op": "set_config",
        "field": "publication",
        "value": {
            **snapshot.config["publication"],
            "qr_mode": "enabled",
            "qr_url": url,
            "qr_label": "Synthetic tournament",
        },
    }


def save(repository, snapshot, base=None):
    with repository.lock():
        return repository.save(snapshot, base)


def test_canonical_template_preserves_unknowns_instead_of_inventing_values(config):
    raw = json.loads(template_path().read_text("utf-8-sig"))
    assert config["event"]["event_dates"] is raw["event"]["event_dates"] is None
    assert config["event"]["timezone"] is None
    assert config["timing_defaults"]["court_buffer_minutes"] is None
    assert config["timing_defaults"]["start_grid_minutes"] is None
    assert config["publication"]["qr_mode"] == "unconfigured"
    assert config["divisions"] == [] and config["courts"] == []


@pytest.mark.parametrize(
    "field,value", [("schema_version", "2.0.0"), ("policy_version", "unapproved-policy")]
)
def test_incompatible_contract_versions_are_refused(config, field, value):
    config[field] = value
    with pytest.raises(ValidationError):
        EventConfig.model_validate(config)


def test_confirmed_policy_cannot_be_silently_overridden(config):
    config["policy"]["identity"]["automatic_same_name_merge"] = True
    with pytest.raises(ValidationError, match="approved policy"):
        EventConfig.model_validate(config)


def test_uuid_identity_and_duplicate_keys_are_enforced(snapshot):
    assert UUID(snapshot.id)
    participant = Participant(official_name="é", display_name="e\u0301")
    assert participant.display_name == "é"
    with pytest.raises(ValidationError):
        Participant(id="name-as-id", official_name="Player", display_name="Player")
    with pytest.raises(ValidationError, match="Duplicate IDs"):
        Snapshot(config=snapshot.config, participants=[participant, participant])
    with pytest.raises(ValidationError, match="timezone"):
        Snapshot(config=snapshot.config, created_at=datetime(2026, 1, 1))


def test_unknown_foreign_keys_and_missing_provenance_are_reported(snapshot):
    registration = Registration(
        participant_id=new_id(), original_division_id=new_id(), source_ref={}
    )
    snapshot.registrations.append(registration)
    snapshot.entries.append(
        Entry(registration_id=new_id(), effective_division_id=snapshot.divisions[0].id)
    )
    codes = {i.code for i in validate_references(snapshot)}
    assert {"REFERENCE_INVALID", "SOURCE_REFERENCE_MISSING", "REGISTRATION_UNACCOUNTED"} <= codes


@pytest.mark.parametrize("case", ["duplicate_chain", "withdrawn", "rejected"])
def test_effective_entries_require_accepted_registration_and_one_active_chain(snapshot, case):
    participant = Participant(official_name="Player", display_name="Player")
    source_id = new_id()
    registration = Registration(
        participant_id=participant.id,
        original_division_id=snapshot.divisions[0].id,
        source_ref={"source_file_id": source_id, "raw_text": "Player"},
    )
    other = Division(name="Other division")
    snapshot.participants.append(participant)
    snapshot.sources.append({"id": source_id})
    snapshot.registrations.append(registration)
    snapshot.divisions.append(other)
    snapshot.entries = [
        Entry(registration_id=registration.id, effective_division_id=division.id)
        for division in snapshot.divisions
    ]
    if case == "duplicate_chain":
        assert validate_references(snapshot), (
            "One original registration cannot produce two active qualifications"
        )
    else:
        snapshot.entries.pop()
        assert validate_references(snapshot) == []
        registration.status = case
        assert validate_references(snapshot), "Inactive registrations must not have active entries"


def test_stale_patch_is_rejected_before_any_mutation(snapshot):
    before = snapshot.model_dump()
    patch = patch_for(snapshot, [publication_operation(snapshot)])
    patch["base_revision_id"] = new_id()
    with pytest.raises(RepositoryError, match="stale"):
        apply_patch(snapshot, patch)
    assert snapshot.model_dump() == before


def test_two_operation_patch_failure_does_not_commit_first_operation(snapshot, tmp_path):
    repository = Repository(tmp_path / "tournament")
    current = save(repository, snapshot)
    before = current.model_dump()
    pointer = (repository.root / "working/current.json").read_bytes()
    patch = patch_for(current, [publication_operation(current), {"op": "unsupported_operation"}])
    with pytest.raises(ValueError, match="Unsupported"):
        apply_patch(current, patch)
    assert current.model_dump() == before
    assert (repository.root / "working/current.json").read_bytes() == pointer
    assert repository.load().model_dump() == before


def test_format_approval_is_content_bound_and_does_not_mutate_input(snapshot):
    division = snapshot.divisions[0]
    before = deepcopy(snapshot.model_dump())
    basis = target_basis(division.format_plan)
    approved, report = approve(
        snapshot, f"format:{division.id}", basis, "reviewer", "Reviewed format"
    )
    assert snapshot.model_dump() == before
    assert approved.divisions[0].format_plan["status"] == "approved"
    assert approval_valid(approved, "format", division.id, approved.divisions[0].format_plan)
    assert report["approval"]["actor_id"] == "reviewer"
    approved.divisions[0].format_plan["parameters"]["changed"] = True
    assert not approval_valid(approved, "format", division.id, approved.divisions[0].format_plan)
    with pytest.raises(ReviewRequired, match="stale"):
        approve(
            approved, f"format:{division.id}", basis, "reviewer", "Cannot approve stale content"
        )


def test_explicitly_stale_approval_cannot_be_reused_with_matching_basis(snapshot):
    division = snapshot.divisions[0]
    approved, _ = approve(
        snapshot,
        f"format:{division.id}",
        target_basis(division.format_plan),
        "reviewer",
        "Reviewed",
    )
    approved.approvals[0]["status"] = "stale"
    assert not approval_valid(approved, "format", division.id, approved.divisions[0].format_plan)


def test_approved_revision_remains_byte_identical_after_qr_amendment(snapshot, tmp_path):
    repository = Repository(tmp_path / "tournament")
    approved, _ = approve(
        snapshot,
        f"format:{snapshot.divisions[0].id}",
        target_basis(snapshot.divisions[0].format_plan),
        "reviewer",
        "Reviewed",
    )
    # Repository immutability is independent of G5 publication completeness.
    approved.status = "approved"
    original = save(repository, approved)
    archived_path = repository.root / "revisions" / original.id / "snapshot.json"
    original_bytes = archived_path.read_bytes()
    original_hash = content_hash(original)
    amended, change = apply_patch(original, patch_for(original, [publication_operation(original)]))
    assert original.status == "approved"
    assert original.config["publication"]["qr_url"] is None
    assert amended.status != "approved"
    assert change["before_hash"] == original_hash
    assert change["after_hash"] == content_hash(amended) != original_hash
    newer = save(repository, amended, original.id)
    assert newer.id != original.id and newer.parent_id == original.id
    assert repository.load().id == newer.id
    assert repository.load(original.id).status == "approved"
    assert archived_path.read_bytes() == original_bytes


def test_repository_stale_save_never_changes_current_revision(snapshot, tmp_path):
    repository = Repository(tmp_path / "tournament")
    current = save(repository, snapshot)
    before = (repository.root / "working/current.json").read_bytes()
    with pytest.raises(RepositoryError, match="stale"):
        save(repository, current, new_id())
    assert (repository.root / "working/current.json").read_bytes() == before
    assert len(list((repository.root / "revisions").iterdir())) == 1


@pytest.mark.parametrize("field,value", [("status", "superseded"), ("config", {})])
def test_snapshot_checksum_detects_content_and_audit_field_corruption(
    snapshot, tmp_path, field, value
):
    repository = Repository(tmp_path / "tournament")
    saved = save(repository, snapshot)
    current_path = repository.root / "working/current.json"
    envelope = json.loads(current_path.read_text("utf-8"))
    envelope["snapshot"][field] = value
    current_path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(RepositoryError, match="checksum"):
        repository.load()
    assert repository.load(saved.id).id == saved.id


def test_initial_source_archive_refuses_digest_mismatch(tmp_path):
    repository = Repository(tmp_path / "tournament")
    source = tmp_path / "source.docx"
    source.write_bytes(b"changed after parsing")
    metadata = {"id": new_id(), "sha256": hashlib.sha256(b"original parsed bytes").hexdigest()}
    with pytest.raises(RepositoryError, match="checksum|digest|changed"):
        repository.archive_source(source, metadata)
    assert not (repository.root / "source" / metadata["id"]).exists()


def test_source_archive_is_immutable_and_existing_corruption_is_refused(tmp_path):
    repository = Repository(tmp_path / "tournament")
    source = tmp_path / "source.docx"
    source.write_bytes(b"original source")
    metadata = {"id": new_id(), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    repository.archive_source(source, metadata)
    archived = repository.root / "source" / metadata["id"] / "original"
    assert archived.read_bytes() == b"original source"
    repository.archive_source(source, metadata)
    archived.write_bytes(b"corrupt archive")
    with pytest.raises(RepositoryError, match="checksum"):
        repository.archive_source(source, metadata)
    assert archived.read_bytes() == b"corrupt archive"


def test_single_writer_lock_conflict_and_exception_cleanup(tmp_path):
    repository = Repository(tmp_path / "tournament")
    with pytest.raises(RuntimeError, match="synthetic failure"), repository.lock():
        with pytest.raises(RepositoryError, match="locked"), Repository(repository.root).lock():
            pytest.fail("Second writer acquired the same tournament")
        raise RuntimeError("synthetic failure")
    assert not (repository.root / ".write.lock").exists()
    with repository.lock():
        assert (repository.root / ".write.lock").exists()


def test_failed_atomic_replace_preserves_existing_document_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    destination = tmp_path / "document.json"
    atomic_json(destination, {"state": "original"})

    def fail_replace(source, target):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        atomic_json(destination, {"state": "replacement"})
    assert json.loads(destination.read_text("utf-8")) == {"state": "original"}
    assert not list(tmp_path.glob(".pending-*"))


def test_transient_windows_rename_denial_retries_atomically_without_losing_prior_data(
    tmp_path, monkeypatch
):
    destination = tmp_path / "document.json"
    atomic_json(destination, {"state": "prior"})
    replace = storage.os.replace
    attempts = []

    def temporarily_denied(source, target):
        attempts.append(1)
        if len(attempts) < 3:
            assert json.loads(destination.read_text("utf-8")) == {"state": "prior"}
            raise PermissionError("Synthetic transient Windows sharing denial")
        return replace(source, target)

    monkeypatch.setattr(storage.os, "replace", temporarily_denied)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    atomic_json(destination, {"state": "next"})
    assert len(attempts) == 3
    assert json.loads(destination.read_text("utf-8")) == {"state": "next"}
