"""Explicit identity resolution, directed merge suggestions, and approved transfers."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, NoReturn

from squash_scheduler.domain.models import (
    Division,
    Entry,
    Issue,
    Participant,
    Registration,
    Snapshot,
    new_id,
    stable_id,
    utcnow,
)
from squash_scheduler.importers.legacy_doc import RosterError


def _fail(code: str, message: str) -> NoReturn:
    raise RosterError(code, message)


def _invalidate(snapshot: Snapshot, affected: set[str]) -> None:
    if not affected:
        return
    for division in snapshot.divisions:
        if division.id in affected and division.format_plan:
            division.format_plan["status"] = "review_required"
            division.format_plan.pop("approval_id", None)
    for draw in snapshot.draws:
        if draw.get("division_id") in affected:
            draw["status"] = "superseded"
    targets = affected | {m.id for m in snapshot.matches if m.division_id in affected}
    targets.update(d["id"] for d in snapshot.draws if d.get("division_id") in affected)
    for approval in snapshot.approvals:
        if approval.get("target_id") in targets or approval.get("target_type") in (
            "revision",
            "schedule",
        ):
            approval["status"] = "stale"
    snapshot.status = "review_required"


def resolve_roster(snapshot: Snapshot, patch: dict) -> dict:
    """Validate the entire patch before committing any identity or entry changes."""
    from squash_scheduler.importers.word import roster_report

    work = snapshot.model_copy(deep=True)
    batch = next((b for b in work.imports if b["id"] == patch.get("batch_id")), None)
    if batch is None:
        _fail("IMPORT_BATCH_NOT_FOUND", "Unknown roster batch")
    if batch["status"] == "blocked":
        _fail("IMPORT_UNSUPPORTED_STRUCTURE", "Blocked import must be corrected and imported again")
    candidates = {c["id"]: c for c in batch["candidates"]}
    participants = {p.id: p for p in work.participants}
    divisions = {d.id for d in work.divisions}
    registrations = {r.id: r for r in work.registrations}
    occupied = {
        (registrations[e.registration_id].participant_id, e.effective_division_id)
        for e in work.entries
        if e.status == "active" and e.registration_id in registrations
    }
    seen = set()
    affected = set()
    for resolution in patch.get("resolutions", []):
        cid = resolution.get("candidate_id")
        if cid not in candidates or cid in seen:
            _fail("IDENTITY_RESOLUTION_INVALID", "Candidate must exist and occur once per patch")
        seen.add(cid)
        candidate = candidates[cid]
        if candidate["status"] == "ignored_with_reason":
            _fail("IDENTITY_RESOLUTION_INVALID", "Ignored source content is not a roster candidate")
        if candidate.get("resolution"):
            if (
                resolution.get("participant_id") == candidate["resolution"]["participant_id"]
                and resolution.get("division_id", candidate["resolution"]["division_id"])
                == candidate["resolution"]["division_id"]
            ):
                continue
            _fail("IDENTITY_RESOLUTION_INVALID", "Candidate has already been resolved")
        division = resolution.get("division_id", candidate.get("division_id"))
        if candidate.get("division_id") is not None and division != candidate["division_id"]:
            _fail(
                "REGISTRATION_ORIGINAL_DIVISION_CHANGE",
                "Resolve the source division first; moving a known registration requires an approved transfer",
            )
        if division not in divisions:
            _fail("DIVISION_NOT_FOUND", "An existing division must be selected explicitly")
        pid = resolution.get("participant_id")
        create = resolution.get("create_participant")
        if bool(pid) == bool(create):
            _fail("IDENTITY_RESOLUTION_INVALID", "Choose participant_id or create_participant")
        if create:
            values = dict(create)
            values.setdefault("display_name", values.get("official_name", ""))
            values.setdefault("id", stable_id("roster-participant", batch["id"], cid))
            participant = Participant.model_validate(values)
            if participant.id in participants:
                _fail("IDENTITY_RESOLUTION_INVALID", "Participant ID already exists")
            work.participants.append(participant)
            participants[participant.id] = participant
            pid = participant.id
        if pid not in participants or participants[pid].identity_status != "confirmed":
            _fail("IDENTITY_AMBIGUOUS", "Participant identity must be explicitly confirmed")
        if (pid, division) in occupied:
            _fail(
                "DUPLICATE_EFFECTIVE_REGISTRATION",
                "Participant already has an active entry in this division; resolve sources manually",
            )
        occupied.add((pid, division))
        affected.add(division)
        registration = Registration(
            id=stable_id("roster-registration", batch["id"], cid),
            participant_id=pid,
            original_division_id=division,
            source_ref=deepcopy(candidate["source_ref"]),
        )
        entry = Entry(
            id=stable_id("roster-entry", registration.id),
            registration_id=registration.id,
            effective_division_id=division,
        )
        work.registrations.append(registration)
        work.entries.append(entry)
        candidate["resolution"] = {
            "participant_id": pid,
            "registration_id": registration.id,
            "entry_id": entry.id,
            "division_id": division,
        }
        for issue in work.issues:
            if issue.entity_id == cid and issue.code == "IDENTITY_AMBIGUOUS":
                issue.status = "resolved"
                issue.resolution_id = registration.id
        for issue in batch["issues"]:
            if issue.get("entity_id") == cid and issue["code"] == "IDENTITY_AMBIGUOUS":
                issue["status"] = "resolved"
                issue["resolution_id"] = registration.id
    batch["status"] = (
        "applied"
        if all(
            c["status"] == "ignored_with_reason" or c.get("resolution") for c in batch["candidates"]
        )
        else "staged"
    )
    _invalidate(work, affected)
    for field in (
        "participants",
        "registrations",
        "entries",
        "imports",
        "issues",
        "status",
        "divisions",
        "draws",
        "approvals",
    ):
        setattr(snapshot, field, getattr(work, field))
    return roster_report(snapshot, batch)


def _eligible_suggestion(source: Division, target: Division) -> bool:
    return (
        source.id != target.id
        and source.gender_category == target.gender_category
        and source.category == target.category == "competitive"
        and source.level_order is not None
        and target.level_order is not None
        and abs(source.level_order - target.level_order) == 1
    )


def division_check(snapshot: Snapshot) -> dict:
    """Count effective qualifications; suggestions never mutate entries or registrations."""
    counts = Counter(e.effective_division_id for e in snapshot.entries if e.status == "active")
    issues: list[Issue] = []
    suggestions = []
    divisions = {d.id: d for d in snapshot.divisions}
    for division in snapshot.divisions:
        count = counts[division.id]
        if division.status in ("cancelled", "merged", "exhibition_only"):
            continue
        if count == 0:
            issues.append(
                Issue(
                    code="DIVISION_EMPTY",
                    severity="info",
                    gate="G2",
                    entity_type="Division",
                    entity_id=division.id,
                    message="Division has no active entries",
                )
            )
        elif count == 1:
            issues.append(
                Issue(
                    code="DIVISION_SINGLE_ENTRY",
                    gate="G2",
                    entity_type="Division",
                    entity_id=division.id,
                    message="A single entry requires an explicit resolution",
                )
            )
        elif count == 2:
            issues.append(
                Issue(
                    code="DIVISION_TWO_ENTRIES",
                    severity="warning",
                    gate="G2",
                    entity_type="Division",
                    entity_id=division.id,
                    message="Two-entry division requires a recorded treatment",
                )
            )
        elif count < division.minimum_entries:
            issues.append(
                Issue(
                    code="DIVISION_BELOW_MINIMUM",
                    gate="G2",
                    entity_type="Division",
                    entity_id=division.id,
                    message="Configured minimum entry count is not met",
                )
            )
    for rule in snapshot.config.get("merge_whitelist", []):
        source = divisions.get(rule.get("source_division_id"))
        target = divisions.get(rule.get("target_division_id"))
        if source is None or target is None or not _eligible_suggestion(source, target):
            continue
        if source.status in ("cancelled", "merged", "exhibition_only") or target.status in (
            "cancelled",
            "merged",
            "exhibition_only",
        ):
            continue
        scope = rule.get("event_scope")
        event_id = snapshot.config.get("event", {}).get("id")
        if scope is not None and scope != event_id:
            continue
        if 0 < counts[source.id] < source.minimum_entries:
            suggestions.append(
                {
                    "source_division_id": source.id,
                    "target_division_id": target.id,
                    "entry_ids": [
                        e.id
                        for e in snapshot.entries
                        if e.status == "active" and e.effective_division_id == source.id
                    ],
                    "eligibility_basis": rule.get("eligibility_basis"),
                    "status": "proposed",
                    "requires_approval": True,
                }
            )
    return {
        "counts": {d.id: counts[d.id] for d in snapshot.divisions},
        "issues": [i.model_dump(mode="json") for i in issues],
        "suggestions": suggestions,
    }


def transfer_entry(snapshot: Snapshot, operation: dict, actor_id: str, reason: str) -> dict:
    """Apply one approved transfer without changing other registrations by this person."""
    if not actor_id.strip() or not reason.strip():
        _fail("MERGE_APPROVAL_REQUIRED", "Transfer requires an actor and reason")
    eligibility = operation.get("eligibility_review") or {}
    if (
        operation.get("status") != "approved"
        or eligibility.get("status") != "approved"
        or not eligibility.get("basis")
    ):
        _fail(
            "MERGE_APPROVAL_REQUIRED",
            "Transfer requires approval and a documented eligibility decision",
        )
    entry = next((e for e in snapshot.entries if e.id == operation.get("source_entry_id")), None)
    target = next(
        (d for d in snapshot.divisions if d.id == operation.get("target_division_id")), None
    )
    if entry is None or entry.status != "active":
        _fail("TRANSFER_SOURCE_INVALID", "Transfer source entry must still be active")
    source = next((d for d in snapshot.divisions if d.id == entry.effective_division_id), None)
    if target is None or source is None or target.status in ("cancelled", "merged"):
        _fail("DIVISION_NOT_FOUND", "Transfer target must be an available division")
    if source.id == target.id:
        _fail("TRANSFER_CYCLE", "Transfer source and target must differ")
    registration = next((r for r in snapshot.registrations if r.id == entry.registration_id), None)
    if registration is None or registration.status != "accepted":
        _fail("TRANSFER_SOURCE_INVALID", "Source registration must be accepted")
    registrations = {r.id: r for r in snapshot.registrations}
    for existing in snapshot.entries:
        reg = registrations.get(existing.registration_id)
        if (
            existing.status == "active"
            and existing.effective_division_id == target.id
            and reg
            and reg.participant_id == registration.participant_id
        ):
            _fail(
                "DUPLICATE_EFFECTIVE_REGISTRATION",
                "Target division already contains this participant",
            )
        if (
            existing.registration_id == entry.registration_id
            and existing.effective_division_id == target.id
        ):
            _fail(
                "TRANSFER_CYCLE", "Transfer would revisit a division in this registration's history"
            )
    rules = snapshot.config.get("merge_whitelist", [])
    event_id = snapshot.config.get("event", {}).get("id")
    ordinary = _eligible_suggestion(source, target) and any(
        r.get("source_division_id") == source.id
        and r.get("target_division_id") == target.id
        and (r.get("event_scope") is None or r["event_scope"] == event_id)
        for r in rules
    )
    if not ordinary and operation.get("special_request") is not True:
        _fail(
            "TRANSFER_SPECIAL_REQUEST_REQUIRED",
            "Transfer outside directed policy requires an approved special request",
        )
    affected = {source.id, target.id}
    matches = [m for m in snapshot.matches if m.division_id in affected]
    if any(m.status in ("in_progress", "completed") for m in matches):
        _fail(
            "TRANSFER_COMPETITION_STARTED",
            "Started competition requires an explicit result-aware amendment",
        )
    request_id = operation.get("id") or new_id()
    new_entry = Entry(
        id=stable_id("transferred-entry", request_id),
        registration_id=entry.registration_id,
        effective_division_id=target.id,
    )
    if any(e.id == new_entry.id for e in snapshot.entries):
        _fail("TRANSFER_ALREADY_APPLIED", "Transfer request ID has already been applied")
    request: dict[str, Any] = {
        "id": request_id,
        "type": "transfer",
        "status": "applied",
        "registration_id": registration.id,
        "source_entry_id": entry.id,
        "source_division_id": source.id,
        "target_division_id": target.id,
        "target_entry_id": new_entry.id,
        "actor_id": actor_id,
        "reason": reason,
        "eligibility_review": deepcopy(eligibility),
        "created_at": utcnow().isoformat(),
        "special_request": bool(operation.get("special_request")),
        "affected_match_ids": [m.id for m in matches],
    }
    entry.status = "transferred"
    snapshot.entries.append(new_entry)
    snapshot.changes.append(request)
    _invalidate(snapshot, affected)
    report = division_check(snapshot)
    return {
        "transfer": request,
        "counts": report["counts"],
        "issues": report["issues"],
        "requires_regeneration": ["formats", "draws", "matches", "schedule"],
    }
