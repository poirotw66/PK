"""Content-bound withdrawal and replacement proposals with immutable match history."""

from __future__ import annotations

from typing import Any

from .matches import possible_participants
from .models import Entry, EntrySide, Snapshot, canonical_hash, content_hash, new_id, utcnow
from .rankings import import_results, resolve_side


def _entry(snapshot: Snapshot, entry_id: str) -> Entry:
    value = next((e for e in snapshot.entries if e.id == entry_id), None)
    if value is None:
        raise ValueError("Entry ID does not exist")
    return value


def _person(snapshot: Snapshot, entry_id: str) -> str:
    value = _entry(snapshot, entry_id)
    registration = next((r for r in snapshot.registrations if r.id == value.registration_id), None)
    if registration is None:
        raise ValueError("Entry requires an existing source registration")
    return registration.participant_id


def _simulate(snapshot: Snapshot, operation: dict[str, Any]) -> tuple[Snapshot, dict[str, Any]]:
    working = snapshot.model_copy(deep=True)
    entry = _entry(working, operation["entry_id"])
    if entry.status != "active":
        raise ValueError("Only an active entry can withdraw or be replaced")
    if working.draws and not working.matches:
        raise ValueError(
            "Draw has no generated matches; generate and review the match graph before changing entries"
        )
    person = _person(working, entry.id)
    people = possible_participants(working)
    division_matches = {
        m.id for m in working.matches if m.division_id == entry.effective_division_id
    }
    affected = {
        mid
        for mid, candidates in people.items()
        if person in candidates and mid in division_matches
    }
    existing_results = {r.get("match_id") for r in working.results}
    direct_ids = {
        m.id
        for m in working.matches
        if entry.id in {resolve_side(working, m.side_a), resolve_side(working, m.side_b)}
    }
    if any(m.id in affected and m.status == "in_progress" for m in working.matches):
        raise ValueError("Affected match is in progress; competition review is required")
    if any(
        m.id in affected
        and m.status not in {"completed", "cancelled", "excluded"}
        and any(s.type == "draw_slot" for s in (m.side_a, m.side_b))
        for m in working.matches
    ):
        raise ValueError("Draw-slot entry changes require a new reviewed draw declaration")
    result_ids: list[str] = []
    rebound_ids: list[str] = []
    replacement_id = operation.get("replacement_entry_id")
    if operation["action"] == "replace":
        if not isinstance(replacement_id, str):
            raise ValueError("Replacement requires an existing entry ID")
        replacement = _entry(working, replacement_id)
        if (
            replacement.id == entry.id
            or replacement.effective_division_id != entry.effective_division_id
        ):
            raise ValueError("Replacement must be a different entry in the same effective division")
        registration = next(r for r in working.registrations if r.id == replacement.registration_id)
        replacement_person = _person(working, replacement.id)
        participant = next((p for p in working.participants if p.id == replacement_person), None)
        if (
            registration.status != "accepted"
            or participant is None
            or participant.identity_status != "confirmed"
        ):
            raise ValueError(
                "Replacement requires an accepted registration and confirmed participant"
            )
        if replacement.status not in {"active", "cancelled"}:
            raise ValueError("Replacement entry is not eligible for activation")
        if replacement_person == person:
            raise ValueError("Replacement must refer to a different source participant ID")
        if any(
            replacement_person in candidates
            for mid, candidates in people.items()
            if mid in division_matches
        ) or any(replacement.id in pool.entry_ids for pool in working.pools):
            raise ValueError("Replacement already participates in the competition")
        if any(
            e.id != replacement.id
            and e.status == "active"
            and e.effective_division_id == replacement.effective_division_id
            and _person(working, e.id) == replacement_person
            for e in working.entries
        ):
            raise ValueError("Replacement participant has another active entry in this division")
        if any(
            m.id in affected
            and (m.status in {"in_progress", "completed"} or m.id in existing_results)
            for m in working.matches
        ):
            raise ValueError(
                "Replacement cannot rewrite affected started matches or existing results"
            )
        if any(
            a.match_id in affected and (a.locks.get("sides") or a.locks.get("draw"))
            for a in working.assignments
        ):
            raise ValueError("Replacement would violate an existing side or draw lock")
        # Draw-slot and deferred declarations carry their own approved candidate identity.
        # Changing those records in place would rewrite the draw evidence.
        if any(
            m.id in affected and any(s.type == "draw_slot" for s in (m.side_a, m.side_b))
            for m in working.matches
        ):
            raise ValueError("Draw-slot replacement requires a new reviewed draw declaration")
        for match in working.matches:
            changed = False
            for name in ("side_a", "side_b"):
                side = getattr(match, name)
                if isinstance(side, EntrySide) and side.entry_id == entry.id:
                    setattr(match, name, EntrySide(entry_id=replacement.id))
                    changed = True
            if changed:
                rebound_ids.append(match.id)
        for pool in working.pools:
            pool.entry_ids = [replacement.id if eid == entry.id else eid for eid in pool.entry_ids]
        replacement.status = "active"
    else:
        # Resolve each successive round on the copy. No existing result, including a
        # pending adjudication, can be replaced by this administrative operation.
        while True:
            ready: list[dict[str, Any]] = []
            for match in working.matches:
                if match.status in {"completed", "cancelled", "excluded"}:
                    continue
                first, second = (
                    resolve_side(working, match.side_a),
                    resolve_side(working, match.side_b),
                )
                if entry.id not in {first, second}:
                    continue
                if match.id in existing_results:
                    raise ValueError("Withdrawal cannot overwrite an existing result")
                opponent = second if first == entry.id else first
                if opponent is None:
                    raise ValueError(
                        "Withdrawal opponent is unresolved; a reviewed advancement plan is required"
                    )
                if opponent == entry.id or _entry(working, opponent).status != "active":
                    raise ValueError(
                        "Withdrawal needs an active distinct opponent; adjudicate both absent sides explicitly"
                    )
                ready.append(
                    {
                        "match_id": match.id,
                        "outcome": "walkover",
                        "winner_id": opponent,
                        "games": [],
                        "status": "confirmed",
                    }
                )
            if not ready:
                break
            import_results(
                working,
                {
                    "results": ready,
                    "actor_id": operation["actor_id"],
                    "reason": operation["reason"],
                },
            )
            result_ids.extend(item["match_id"] for item in ready)
    entry.status = "withdrawn"
    # Keep the source registration and participant records intact: another division
    # may use the same registration, and historical results still refer to this entry.
    stages = {m.stage_id for m in working.matches if m.id in affected}
    draw_divisions = {draw["id"]: draw.get("division_id") for draw in working.draws}
    ranking_stages = {
        decision["id"]: decision.get("stage_id") for decision in working.ranking_decisions
    }
    for approval in working.approvals:
        kind, target = approval.get("target_type"), approval.get("target_id")
        if (
            kind in {"revision", "schedule"}
            or kind in {"format", "division_count"}
            and target == entry.effective_division_id
            or kind in {"draw", "deferred_draw"}
            and draw_divisions.get(target) == entry.effective_division_id
            or kind == "ranking"
            and ranking_stages.get(target) in stages
        ):
            approval["status"] = "stale"
    for decision in working.ranking_decisions:
        if decision.get("stage_id") in stages:
            decision["status"] = "stale"
    if not working.matches:
        division = next(d for d in working.divisions if d.id == entry.effective_division_id)
        if division.format_plan:
            division.format_plan.update(status="draft", approval_id=None)
    working.status = "review_required"
    return working, {
        "affected_match_ids": sorted(affected),
        "resolved_match_ids": sorted(direct_ids),
        "walkover_match_ids": sorted(result_ids),
        "rebound_match_ids": sorted(rebound_ids),
        "preserved_result_count": len(snapshot.results),
        "division_id": entry.effective_division_id,
    }


def propose_change(
    snapshot: Snapshot,
    entry_id: str,
    action: str = "withdraw",
    replacement_entry_id: str | None = None,
    reason: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """Dry run an operation without modifying the input; reject unsafe transitions."""
    if action not in {"withdraw", "replace"}:
        raise ValueError("Action must be withdraw or replace")
    if (
        not isinstance(actor, str)
        or not isinstance(reason, str)
        or not actor.strip()
        or not reason.strip()
    ):
        raise ValueError("Change requires actor and reason")
    if (action == "replace") != (replacement_entry_id is not None):
        raise ValueError("Only replacement requires replacement_entry_id")
    operation = {
        "entry_id": entry_id,
        "action": action,
        "replacement_entry_id": replacement_entry_id,
        "actor_id": actor,
        "reason": reason,
    }
    _, impact = _simulate(snapshot, operation)
    proposal = {
        "kind": "entry_change",
        "base_revision_id": snapshot.id,
        "basis_hash": canonical_hash(snapshot),
        "operation": operation,
        "impact": impact,
    }
    return {**proposal, "proposal_hash": canonical_hash(proposal)}


def apply_change(snapshot: Snapshot, proposal: dict[str, Any]) -> tuple[Snapshot, dict[str, Any]]:
    """Apply exactly the dry-run proposal atomically and append an audit record."""
    if proposal.get("base_revision_id") != snapshot.id or proposal.get(
        "basis_hash"
    ) != canonical_hash(snapshot):
        raise ValueError("Entry change proposal basis is stale; run a new dry run")
    operation = proposal.get("operation", {})
    expected = propose_change(
        snapshot,
        operation.get("entry_id"),
        operation.get("action"),
        operation.get("replacement_entry_id"),
        operation.get("reason", ""),
        operation.get("actor_id", ""),
    )
    if proposal != expected:
        raise ValueError("Entry change proposal was modified; run a new dry run")
    working, impact = _simulate(snapshot, operation)
    before_evidence = snapshot.model_dump(mode="json")
    # Each baseline needs only the preceding operational review. Keeping all older
    # embedded copies would duplicate the entire audit chain at every change.
    prior_reviews = [c for c in before_evidence["changes"] if c.get("kind") == "entry_change"]
    before_evidence["changes"] = prior_reviews[-1:]
    audit = {
        "id": new_id(),
        "base_revision_id": snapshot.id,
        "actor_id": operation["actor_id"],
        "reason": operation["reason"],
        "operations": [{"op": "entry_change", **operation}],
        "impact": impact,
        "proposal_hash": proposal["proposal_hash"],
        "created_at": utcnow().isoformat(),
        "before_hash": content_hash(snapshot),
        "after_hash": content_hash(working),
        "kind": "entry_change",
        "status": "draft",
        "approval_id": None,
        "before_snapshot": before_evidence,
        "before_snapshot_hash": canonical_hash(before_evidence),
        "topology_hash": entry_change_basis(working),
        "result_evidence": working.results,
        "match_statuses": {m.id: m.status for m in working.matches},
    }
    working.changes.append(audit)
    return working, audit


def register_reserve(snapshot: Snapshot, entry_id: str, actor: str, reason: str) -> dict[str, Any]:
    """Keep an accepted, unplaced registration as an explicitly audited reserve."""
    if not actor.strip() or not reason.strip():
        raise ValueError("Reserve registration requires actor and reason")
    entry = _entry(snapshot, entry_id)
    registration = next(r for r in snapshot.registrations if r.id == entry.registration_id)
    participant = next(p for p in snapshot.participants if p.id == registration.participant_id)
    if registration.status != "accepted" or participant.identity_status != "confirmed":
        raise ValueError("A reserve requires accepted registration and confirmed identity")
    if entry.status not in {"active", "cancelled"}:
        raise ValueError("This entry cannot become a reserve")
    if (
        any(entry.id in pool.entry_ids for pool in snapshot.pools)
        or any(
            entry.id == getattr(side, "entry_id", None)
            for match in snapshot.matches
            for side in (match.side_a, match.side_b)
        )
        or any(entry.id in str(record) for record in snapshot.draws)
    ):
        raise ValueError(
            "An already placed entry needs a reviewed withdrawal, not reserve registration"
        )
    # Cancel only the active qualification, never its source registration. A reviewed
    # replacement can activate it later; the audit distinguishes it from cancellation.
    entry.status = "cancelled"
    return {
        "entry_id": entry.id,
        "participant_id": participant.id,
        "state": "reserve",
        "actor_id": actor,
        "reason": reason,
    }


def entry_change_basis(snapshot: Snapshot) -> str:
    """Bind roster, rules and exact graph; independent scheduling/results may progress."""
    data = snapshot.model_dump(mode="json")
    for key in (
        "id",
        "parent_id",
        "created_at",
        "status",
        "assignments",
        "approvals",
        "changes",
        "results",
        "ranking_decisions",
        "issues",
    ):
        data.pop(key, None)
    for match in data["matches"]:
        match.pop("status")
    return canonical_hash(data)


def entry_change_review_issues(snapshot: Snapshot, audit: dict[str, Any]) -> list[str]:
    """Verify original evidence and the precise deterministic operational transition."""
    from .rankings import _validate_result, effective_rules
    from .validation import validate

    try:
        if audit.get("kind") != "entry_change":
            raise ValueError("Entry-change audit kind is missing")
        before = Snapshot.model_validate(audit["before_snapshot"])
        if canonical_hash(before) != audit["before_snapshot_hash"]:
            raise ValueError("Original entry-change evidence was modified")
        if audit.get("topology_hash") != entry_change_basis(snapshot):
            raise ValueError("Reviewed roster, rules or match topology changed")
        original_report = validate(before, "G3")
        if any(
            i["gate"] in {"G2", "G3"} and i["severity"] == "error" and i["status"] == "open"
            for i in original_report["issues"]
        ):
            raise ValueError("Original competition requires valid format/draw/graph approvals")
        operations = audit["operations"]
        if len(operations) != 1 or operations[0].get("op") != "entry_change":
            raise ValueError("Audit must contain exactly one entry-change operation")
        expected, impact = _simulate(before, operations[0])
        if entry_change_basis(expected) != audit["topology_hash"] or impact != audit["impact"]:
            raise ValueError("Entry-change evidence does not reproduce the reviewed transition")
        if audit["match_statuses"] != {m.id: m.status for m in expected.matches}:
            raise ValueError("Audited match statuses do not match the permitted transition")

        def result_basis(result: dict[str, Any]) -> str:
            return canonical_hash(
                {k: v for k, v in result.items() if k not in {"id", "created_at"}}
            )

        evidence = audit["result_evidence"]
        if sorted(map(result_basis, evidence)) != sorted(map(result_basis, expected.results)):
            raise ValueError("Audited results do not match the permitted withdrawal outcomes")
        if any(r not in snapshot.results for r in evidence):
            raise ValueError("Original or withdrawal result evidence changed")
        matches = {m.id: m for m in snapshot.matches}
        for result in snapshot.results:
            if result not in evidence:
                match = matches[result["match_id"]]
                _validate_result(
                    snapshot,
                    match,
                    result,
                    result.get("rule_snapshot") or effective_rules(snapshot, match.stage_id),
                )
        for match_id, previous_status in audit["match_statuses"].items():
            current = matches[match_id].status
            if (
                previous_status in {"completed", "cancelled", "excluded"}
                and current != previous_status
            ):
                raise ValueError("Historical match status changed")
            if previous_status == "in_progress" and current not in {"in_progress", "completed"}:
                raise ValueError("Started historical match status regressed")
            if previous_status == "ready" and current == "planned":
                raise ValueError("Ready historical match status regressed")
            if current in {"cancelled", "excluded"} and current != previous_status:
                raise ValueError("Entry review cannot authorize new match exclusions")
            if (
                current == "completed"
                and previous_status != "completed"
                and not any(
                    r.get("match_id") == match_id
                    and r.get("status") == "confirmed"
                    and r.get("outcome") not in {"pending", "cancelled"}
                    for r in snapshot.results
                )
            ):
                raise ValueError("Completed match progression requires a valid confirmed result")
        # Historical approval evidence must also remain in the actual audit trail.
        for approval in before.approvals:
            basis = {k: v for k, v in approval.items() if k != "status"}
            if not any(
                {k: v for k, v in a.items() if k != "status"} == basis for a in snapshot.approvals
            ):
                raise ValueError("Original approval audit evidence is missing or changed")
    except (ValueError, KeyError, TypeError, StopIteration, RecursionError) as exc:
        return [str(exc)]
    return []
