"""Reviewed future draws with typed qualification sources and immutable execution logs.

Declarations name complete candidate sets, the procedure and its timing. Executions
are new records; they never overwrite the declaration or rebind matches themselves.
``executed_at`` records completion of the declared draw procedure.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from .models import (
    ID,
    DrawSlotSide,
    EntrySide,
    Issue,
    LoserSide,
    Model,
    PoolRankSide,
    Snapshot,
    WinnerSide,
    canonical_hash,
    new_id,
    utcnow,
)
from .validation import approval_valid

CandidateSide = Annotated[
    EntrySide | PoolRankSide | WinnerSide | LoserSide, Field(discriminator="type")
]


class _Slot(Model):
    slot_id: str = Field(min_length=1)
    candidate_sides: list[CandidateSide] = Field(min_length=1)

    @field_validator("slot_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Slot IDs must be nonblank")
        return value


class _Procedure(Model):
    description: str = Field(min_length=1)
    execution_trigger: Literal["after_sources_confirmed"]
    duration_minutes: int = Field(ge=0, strict=True)
    earliest_at: datetime | None

    @field_validator("description")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Procedure description must be nonblank")
        return value

    @field_validator("earliest_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Execution timing requires a timezone")
        return value


class _PositionConstraint(Model):
    candidate_side: CandidateSide
    slot_ids: list[str] = Field(min_length=1)


class _AllowedPositions(Model):
    mode: Literal["disabled", "provided"] = "disabled"
    constraints: list[_PositionConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def explicit_mode(self) -> _AllowedPositions:
        if self.mode == "disabled" and self.constraints:
            raise ValueError("Disabled position constraints must be empty")
        if self.mode == "provided" and not self.constraints:
            raise ValueError("Provided position constraints must be nonempty")
        return self


class _Declaration(Model):
    division_id: ID
    slots: list[_Slot] = Field(min_length=1)
    procedure: _Procedure
    allowed_positions: _AllowedPositions = Field(default_factory=_AllowedPositions)

    @model_validator(mode="after")
    def positions(self) -> _Declaration:
        ids = [slot.slot_id for slot in self.slots]
        if len(set(ids)) != len(ids):
            raise ValueError("Deferred slot IDs must be unique")
        candidates = {canonical_hash(side) for slot in self.slots for side in slot.candidate_sides}
        if len(candidates) != len(ids):
            raise ValueError("A complete draw must provide one slot per distinct qualification")
        for slot in self.slots:
            if len({canonical_hash(s) for s in slot.candidate_sides}) != len(slot.candidate_sides):
                raise ValueError("Slot candidate sources must be unique")
        constrained = set()
        for constraint in self.allowed_positions.constraints:
            key = canonical_hash(constraint.candidate_side)
            if key not in candidates or key in constrained:
                raise ValueError("Position constraint must identify a unique declared source")
            constrained.add(key)
            if len(set(constraint.slot_ids)) != len(constraint.slot_ids) or not set(
                constraint.slot_ids
            ) <= set(ids):
                raise ValueError("Position constraints reference invalid or duplicate slots")
        allowed = {
            canonical_hash(c.candidate_side): set(c.slot_ids)
            for c in self.allowed_positions.constraints
        }
        choices = {
            slot.slot_id: [
                canonical_hash(candidate)
                for candidate in slot.candidate_sides
                if canonical_hash(candidate) not in allowed
                or slot.slot_id in allowed[canonical_hash(candidate)]
            ]
            for slot in self.slots
        }
        owners: dict[str, str] = {}

        def assign(slot_id: str, visited: set[str]) -> bool:
            for candidate in choices[slot_id]:
                if candidate in visited:
                    continue
                visited.add(candidate)
                if candidate not in owners or assign(owners[candidate], visited):
                    owners[candidate] = slot_id
                    return True
            return False

        if not all(assign(slot_id, set()) for slot_id in ids):
            raise ValueError("Deferred draw position constraints cannot fill every slot")
        return self


class _SlotAssignment(Model):
    slot_id: str = Field(min_length=1)
    entry_id: ID


class _Execution(Model):
    draw_run_id: ID
    assignments: list[_SlotAssignment] = Field(min_length=1)
    executed_at: datetime
    evidence: str = Field(min_length=1)

    @field_validator("executed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Execution time requires a timezone")
        return value

    @field_validator("evidence")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Draw execution evidence must be nonblank")
        return value


def _declaration(record: dict[str, Any]) -> _Declaration:
    return _Declaration.model_validate(
        {key: record[key] for key in _Declaration.model_fields if key in record}
    )


def _sources(snapshot: Snapshot, declaration: _Declaration) -> dict[str, Any]:
    """Walk only typed candidate ancestry, rejecting cycles and nested deferred draws."""
    from .rankings import effective_rules

    division = next((d for d in snapshot.divisions if d.id == declaration.division_id), None)
    if division is None:
        raise ValueError("Deferred draw division does not exist")
    entries = {e.id: e for e in snapshot.entries}
    registrations = {r.id: r for r in snapshot.registrations}
    people = {p.id: p for p in snapshot.participants}
    stages = {s.id: s for s in snapshot.stages}
    matches = {m.id: m for m in snapshot.matches}
    pools = {p.id: p for p in snapshot.pools}
    captured: dict[str, Any] = {
        "entries": {},
        "matches": {},
        "pools": {},
        "stages": {},
        "draws": {},
    }

    def entry(entry_id: str) -> None:
        value = entries.get(entry_id)
        if value is None or value.effective_division_id != division.id or value.status != "active":
            raise ValueError("Deferred candidate entry must be active in its division")
        registration = registrations.get(value.registration_id)
        person = people.get(registration.participant_id) if registration else None
        if not registration or registration.status != "accepted" or not person:
            raise ValueError("Deferred candidate requires an accepted registration and participant")
        if person.identity_status != "confirmed":
            raise ValueError("Deferred candidate identity must be confirmed")
        captured["entries"][entry_id] = {
            "entry": value.model_dump(mode="json"),
            "registration": registration.model_dump(mode="json"),
            "participant": person.model_dump(mode="json"),
        }

    def stage(stage_id: str) -> None:
        value = stages.get(stage_id)
        if value is None or value.division_id != division.id:
            raise ValueError("Deferred source stage is missing or belongs to another division")
        captured["stages"][stage_id] = {
            "stage": value.model_dump(mode="json"),
            "rules": effective_rules(snapshot, stage_id),
        }

    def match(match_id: str, visiting: set[str]) -> None:
        if match_id in visiting:
            raise ValueError("Deferred candidate source graph contains a cycle")
        if match_id in captured["matches"]:
            return
        value = matches.get(match_id)
        if (
            value is None
            or value.division_id != division.id
            or value.status in {"cancelled", "excluded"}
        ):
            raise ValueError("Deferred source match must be live and in its division")
        stage(value.stage_id)
        for candidate in (value.side_a, value.side_b):
            side(candidate, visiting | {match_id})
        payload = value.model_dump(mode="json")
        # Ordinary result confirmation changes match status, never source topology.
        payload.pop("status")
        captured["matches"][match_id] = payload

    def side(value: Any, visiting: set[str]) -> None:
        if isinstance(value, EntrySide):
            entry(value.entry_id)
        elif isinstance(value, (WinnerSide, LoserSide)):
            match(value.match_id, visiting)
        elif isinstance(value, PoolRankSide):
            pool = pools.get(value.pool_id)
            if pool is None or value.rank > pool.advancement_count:
                raise ValueError("Deferred pool qualification is missing or exceeds advancement")
            stage(pool.stage_id)
            if stages[pool.stage_id].type != "round_robin":
                raise ValueError("Deferred pool qualification requires a round-robin stage")
            for member in pool.entry_ids:
                entry(member)
            captured["pools"][pool.id] = pool.model_dump(mode="json")
            for source in _pool_matches(snapshot, pool.id):
                match(source.id, visiting)
        elif isinstance(value, DrawSlotSide):
            from .draws import draw_inputs

            draw = next((d for d in snapshot.draws if d.get("id") == value.draw_run_id), None)
            if (
                draw is None
                or draw.get("kind") == "deferred_slots"
                or draw.get("status") != "approved"
                or draw.get("division_id") != division.id
                or not approval_valid(snapshot, "draw", draw["id"], draw)
            ):
                raise ValueError(
                    "Source ancestry requires a current ordinary draw, not a deferred draw"
                )
            if draw.get("input_hash") != canonical_hash(
                draw_inputs(snapshot, division.id, draw["random_seed"])
            ):
                raise ValueError("Source ancestry draw inputs changed")
            output = next(
                (
                    slot
                    for slot in (draw.get("output") or {}).get("slots", [])
                    if str(slot.get("slot_id")) == value.slot_id
                ),
                None,
            )
            if output is None:
                raise ValueError("Source ancestry draw slot does not exist")
            entry(output.get("entry_id"))
            captured["draws"][draw["id"]] = {
                key: item for key, item in draw.items() if key not in {"status", "approval_id"}
            }
        else:
            raise ValueError(  # noqa: TRY004 - public operations consistently reject bad input.
                "Deferred source ancestry cannot contain byes or deferred draw slots"
            )

    for slot in declaration.slots:
        for candidate in slot.candidate_sides:
            side(candidate, set())
    captured["format"] = {
        k: v for k, v in (division.format_plan or {}).items() if k not in {"status", "approval_id"}
    }
    captured["rules"] = {
        "division": division.rule_overrides,
        "draw": {**snapshot.config.get("draw_defaults", {}), **division.draw_overrides},
    }
    return captured


def _pool_matches(snapshot: Snapshot, pool_id: str) -> list[Any]:
    pool = next(p for p in snapshot.pools if p.id == pool_id)
    members = set(pool.entry_ids)
    return [
        match
        for match in snapshot.matches
        if match.stage_id == pool.stage_id
        and isinstance(match.side_a, EntrySide)
        and isinstance(match.side_b, EntrySide)
        and {match.side_a.entry_id, match.side_b.entry_id} <= members
    ]


def input_basis(snapshot: Snapshot, record: dict[str, Any]) -> str:
    """Hash source topology and eligibility, excluding ordinary results progression."""
    declaration = _declaration(record)
    return canonical_hash(
        {
            "declaration": declaration.model_dump(mode="json"),
            "sources": _sources(snapshot, declaration),
        }
    )


def propose_deferred_draw(
    snapshot: Snapshot, operation: dict[str, Any], actor: str, reason: str
) -> dict[str, Any]:
    if not actor.strip() or not reason.strip():
        raise ValueError("Deferred draw requires actor and reason")
    declaration = _Declaration.model_validate({k: v for k, v in operation.items() if k != "op"})
    record = {
        "id": new_id(),
        "kind": "deferred_slots",
        "phase": "declaration",
        **declaration.model_dump(mode="json"),
        "actor_id": actor,
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "status": "draft",
        "approval_id": None,
    }
    record["input_hash"] = input_basis(snapshot, record)
    snapshot.draws.append(record)
    return record


def validate_deferred_draw(snapshot: Snapshot, record: dict[str, Any]) -> list[Issue]:
    """Drafts may be reviewed; approved records must have a real current approval."""
    try:
        if record.get("kind") != "deferred_slots" or record.get("phase") not in {
            "declaration",
            "execution",
        }:
            raise ValueError("Unknown deferred draw record kind or phase")
        if input_basis(snapshot, record) != record.get("input_hash"):
            raise ValueError("Deferred draw candidate inputs changed")
        if record.get("status") == "approved" and (
            not approval_valid(snapshot, "deferred_draw", record["id"], record)
            or not any(
                a.get("id") == record.get("approval_id") and a.get("action") == "approve"
                for a in snapshot.approvals
            )
        ):
            raise ValueError("Deferred draw requires a current content-bound approval")
        if record["phase"] == "execution":
            parent = next(
                (d for d in snapshot.draws if d.get("id") == record.get("parent_draw_run_id")),
                None,
            )
            if parent is None or parent.get("phase") != "declaration":
                raise ValueError("Deferred execution requires its original declaration")
            if _declaration(parent) != _declaration(record):
                raise ValueError("Deferred execution must preserve its declared inputs")
            _require_approved(snapshot, parent)
            execution = _Execution.model_validate(
                {
                    "draw_run_id": parent["id"],
                    "assignments": record.get("output", {}).get("slots"),
                    "executed_at": record.get("executed_at"),
                    "evidence": record.get("evidence"),
                }
            )
            _validate_execution(snapshot, parent, execution)
    except (ValueError, KeyError, StopIteration, TypeError, RecursionError) as exc:
        return [
            Issue(
                code="DEFERRED_DRAW_INVALID",
                gate="G3",
                entity_type="draw",
                entity_id=record.get("id"),
                message=str(exc),
            )
        ]
    return []


def _require_approved(snapshot: Snapshot, record: dict[str, Any]) -> None:
    if record.get("status") != "approved":
        raise ValueError("Deferred draw must be approved")
    issues = validate_deferred_draw(snapshot, record)
    if issues:
        raise ValueError("; ".join(i.message for i in issues))


def _record_slot(snapshot: Snapshot, side: DrawSlotSide) -> tuple[dict[str, Any], _Slot]:
    record = next((d for d in snapshot.draws if d.get("id") == side.draw_run_id), None)
    if record is None:
        raise ValueError("Deferred draw run does not exist")
    _require_approved(snapshot, record)
    slot = next((s for s in _declaration(record).slots if s.slot_id == side.slot_id), None)
    if slot is None:
        raise ValueError("Deferred draw slot does not exist")
    return record, slot


def slot_candidates(snapshot: Snapshot, side: DrawSlotSide) -> set[str]:
    """Return every possible participant, never only current provisional qualifiers."""
    record, slot = _record_slot(snapshot, side)
    # A private traversal can restrict this slot without imposing the complete-run
    # slot-count invariant again. The full record was already validated above.
    declaration = _declaration(record)
    declaration = declaration.model_copy(update={"slots": [slot]})
    sources = _sources(snapshot, declaration)
    if record["phase"] == "execution":
        entry_id = next(
            s["entry_id"] for s in record["output"]["slots"] if s["slot_id"] == side.slot_id
        )
        return {sources["entries"][entry_id]["registration"]["participant_id"]}
    return {item["registration"]["participant_id"] for item in sources["entries"].values()}


def _dependencies(snapshot: Snapshot, slot: _Slot) -> set[str]:
    result = set()
    for candidate in slot.candidate_sides:
        if isinstance(candidate, (WinnerSide, LoserSide)):
            result.add(candidate.match_id)
        elif isinstance(candidate, PoolRankSide):
            result.update(m.id for m in _pool_matches(snapshot, candidate.pool_id))
    return result


def slot_dependencies(snapshot: Snapshot, side: DrawSlotSide) -> set[str]:
    record, _ = _record_slot(snapshot, side)
    return set().union(*(_dependencies(snapshot, slot) for slot in _declaration(record).slots))


def slot_duration_minutes(snapshot: Snapshot, side: DrawSlotSide) -> int:
    record, _ = _record_slot(snapshot, side)
    return _declaration(record).procedure.duration_minutes


def _ready_at(snapshot: Snapshot, record: dict[str, Any], slot: _Slot) -> datetime | None:
    from ..scheduling.constraints import effective_timing

    declaration = _declaration(record)
    bounds = [declaration.procedure.earliest_at] if declaration.procedure.earliest_at else []
    assignments = {a.match_id: a for a in snapshot.assignments}
    matches = {m.id: m for m in snapshot.matches}
    pool_deps = {
        m.id
        for candidate in slot.candidate_sides
        if isinstance(candidate, PoolRankSide)
        for m in _pool_matches(snapshot, candidate.pool_id)
    }
    for match_id in _dependencies(snapshot, slot):
        assignment = assignments.get(match_id)
        if assignment is None:
            return None
        lag: Any = 0
        if match_id in pool_deps:
            lag = effective_timing(snapshot, matches[match_id]).get("ranking_confirmation_minutes")
            if type(lag) is not int or lag < 0:
                return None
        bounds.append(assignment.play_end_at + timedelta(minutes=lag))
    return (
        max(bounds) + timedelta(minutes=declaration.procedure.duration_minutes) if bounds else None
    )


def slot_ready_at(snapshot: Snapshot, side: DrawSlotSide) -> datetime | None:
    record, _ = _record_slot(snapshot, side)
    if record["phase"] == "execution":
        return datetime.fromisoformat(record["executed_at"])
    values = [_ready_at(snapshot, record, slot) for slot in _declaration(record).slots]
    if any(value is None for value in values):
        return None
    return max((value for value in values if value is not None), default=None)


def slot_timing(snapshot: Snapshot, side: DrawSlotSide) -> tuple[datetime | None, dict[str, int]]:
    """Absolute readiness and source-relative lags, independent of the old schedule."""
    from ..scheduling.constraints import effective_timing

    record, _ = _record_slot(snapshot, side)
    declaration = _declaration(record)
    duration = declaration.procedure.duration_minutes
    bounds = []
    if declaration.procedure.earliest_at is not None:
        bounds.append(declaration.procedure.earliest_at + timedelta(minutes=duration))
    if record["phase"] == "execution":
        bounds.append(datetime.fromisoformat(record["executed_at"]))
    matches = {m.id: m for m in snapshot.matches}
    lags: dict[str, int] = {}
    for slot in declaration.slots:
        for source in slot.candidate_sides:
            dependencies = _dependencies(
                snapshot, _Slot(slot_id=slot.slot_id, candidate_sides=[source])
            )
            for match_id in dependencies:
                lag = duration
                if isinstance(source, PoolRankSide):
                    lag += effective_timing(snapshot, matches[match_id])[
                        "ranking_confirmation_minutes"
                    ]
                lags[match_id] = max(lags.get(match_id, 0), lag)
    return max(bounds) if bounds else None, lags


def bind_deferred_draw(
    snapshot: Snapshot, draw_run_id: str, bindings: list[dict[str, str]]
) -> list[str]:
    """Bind approved slots atomically without regenerating matches or assignments."""
    from .matches import dependency_map, validate_graph

    if not bindings:
        raise ValueError("Deferred binding requires at least one match side")
    candidate = snapshot.model_copy(deep=True)
    matches = {m.id: m for m in candidate.matches}
    graph = dependency_map(snapshot)
    affected = {b["match_id"] for b in bindings}
    while True:
        expanded = affected | {m for m, deps in graph.items() if deps & affected}
        if expanded == affected:
            break
        affected = expanded
    locked = {
        a.match_id for a in snapshot.assignments if a.locks.get("sides") or a.locks.get("draw")
    }
    if any(
        matches[m].status in {"in_progress", "completed"} or m in locked
        for m in affected
        if m in matches
    ):
        raise ValueError(
            "Deferred binding cannot change started or side/draw-locked match ancestry"
        )
    if any(r.get("match_id") in affected for r in snapshot.results):
        raise ValueError("Deferred binding cannot change match ancestry with recorded results")
    seen = set()
    for binding in bindings:
        match_id, field = binding["match_id"], binding["side"]
        if match_id not in matches or field not in {"side_a", "side_b"}:
            raise ValueError("Deferred binding requires an existing match and side_a or side_b")
        if (match_id, field) in seen:
            raise ValueError("Deferred binding contains a duplicate match side")
        seen.add((match_id, field))
        side = DrawSlotSide(draw_run_id=draw_run_id, slot_id=binding["slot_id"])
        _record_slot(candidate, side)
        setattr(matches[match_id], field, side)
    errors = [i for i in validate_graph(candidate) if i.severity == "error"]
    if errors:
        raise ValueError("; ".join(i.message for i in errors))
    snapshot.matches = candidate.matches
    return sorted(affected)


def bind_deferred_execution(snapshot: Snapshot, execution_id: str) -> list[str]:
    record = next((d for d in snapshot.draws if d.get("id") == execution_id), None)
    if record is None or record.get("phase") != "execution":
        raise ValueError("Deferred execution record does not exist")
    _require_approved(snapshot, record)
    bindings = [
        {"match_id": match.id, "side": field, "slot_id": side.slot_id}
        for match in snapshot.matches
        for field, side in (("side_a", match.side_a), ("side_b", match.side_b))
        if isinstance(side, DrawSlotSide) and side.draw_run_id == record["parent_draw_run_id"]
    ]
    return bind_deferred_draw(snapshot, execution_id, bindings)


def _validate_execution(snapshot: Snapshot, parent: dict[str, Any], execution: _Execution) -> None:
    from .rankings import resolve_side

    declaration = _declaration(parent)
    earliest = declaration.procedure.earliest_at
    if earliest is not None and execution.executed_at < earliest + timedelta(
        minutes=declaration.procedure.duration_minutes
    ):
        raise ValueError("Draw execution precedes declared procedure timing")
    assignments = {s.slot_id: s.entry_id for s in execution.assignments}
    if len(assignments) != len(execution.assignments) or set(assignments) != {
        s.slot_id for s in declaration.slots
    }:
        raise ValueError("Execution must assign every declared slot exactly once")
    if len(set(assignments.values())) != len(assignments):
        raise ValueError("Draw execution entries must be distinct")
    constraints = {
        canonical_hash(c.candidate_side): set(c.slot_ids)
        for c in declaration.allowed_positions.constraints
    }
    # Confirmation is observed before the draw, even when source scheduling has
    # not been imported. Do not let a backdated execution predate known evidence.
    source_ids = set(_sources(snapshot, declaration)["matches"])
    confirmed_times = [
        result["created_at"]
        for result in snapshot.results
        if result.get("match_id") in source_ids
        and result.get("status") == "confirmed"
        and result.get("created_at") is not None
    ]
    for timestamp in confirmed_times:
        confirmed_at = (
            timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(timestamp)
        )
        if confirmed_at.utcoffset() is None:
            raise ValueError("Source result confirmation requires a timezone")
        if execution.executed_at < confirmed_at + timedelta(
            minutes=declaration.procedure.duration_minutes
        ):
            raise ValueError("Draw execution precedes source result confirmation")
    for slot in declaration.slots:
        candidates = set()
        for candidate in slot.candidate_sides:
            entry_id = resolve_side(snapshot, candidate)
            if entry_id is None:
                raise ValueError(
                    "All declared draw qualifications must be confirmed before execution"
                )
            allowed = constraints.get(canonical_hash(candidate))
            if allowed is None or slot.slot_id in allowed:
                candidates.add(entry_id)
        if assignments[slot.slot_id] not in candidates:
            raise ValueError("Assigned entry is not an allowed confirmed candidate for this slot")
        bound = _ready_at(snapshot, parent, slot)
        if bound is not None and execution.executed_at < bound:
            raise ValueError(
                "Draw execution precedes source readiness or declared procedure timing"
            )
    participants = {
        e.id: next(r.participant_id for r in snapshot.registrations if r.id == e.registration_id)
        for e in snapshot.entries
        if e.id in assignments.values()
    }
    if len(set(participants.values())) != len(assignments):
        raise ValueError("Draw execution cannot assign the same participant more than once")


def realize_deferred_draw(
    snapshot: Snapshot, operation: dict[str, Any], actor: str, reason: str
) -> dict[str, Any]:
    if not actor.strip() or not reason.strip():
        raise ValueError("Deferred execution requires actor and reason")
    execution = _Execution.model_validate({k: v for k, v in operation.items() if k != "op"})
    parent = next((d for d in snapshot.draws if d.get("id") == execution.draw_run_id), None)
    if parent is None or parent.get("phase") != "declaration":
        raise ValueError("Execution requires a deferred draw declaration")
    _require_approved(snapshot, parent)
    _validate_execution(snapshot, parent, execution)
    record = {
        "id": new_id(),
        "kind": "deferred_slots",
        "phase": "execution",
        **deepcopy(_declaration(parent).model_dump(mode="json")),
        "parent_draw_run_id": parent["id"],
        "input_hash": parent["input_hash"],
        "output": {"slots": [s.model_dump(mode="json") for s in execution.assignments]},
        "executed_at": execution.executed_at.isoformat(),
        "evidence": execution.evidence,
        "actor_id": actor,
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "status": "draft",
        "approval_id": None,
    }
    snapshot.draws.append(record)
    return record
