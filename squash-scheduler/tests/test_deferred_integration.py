"""Deferred slots participate in actual match graphs, scheduling and rebindings."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_deferred_draws import approve, declaration, execution
from test_formats_draws import event
from test_scheduling import fixture

from squash_scheduler.domain.deferred_draws import (
    bind_deferred_draw,
    bind_deferred_execution,
    propose_deferred_draw,
    realize_deferred_draw,
)
from squash_scheduler.domain.matches import dependency_map, possible_participants, validate_graph
from squash_scheduler.domain.models import EntrySide, Match, Stage, WinnerSide
from squash_scheduler.domain.rankings import resolve_side
from squash_scheduler.domain.validation import target_basis, validate
from squash_scheduler.scheduling import solve_schedule, validate_schedule


def setup_draw(sources=None):
    snapshot, division = event(4)
    timing = fixture(courts=2).config
    timing["event"] = {"timezone": "UTC", "event_dates": ["2030-01-01"]}
    timing["timing_defaults"]["daily_grid_anchors"] = [
        {"date": "2030-01-01", "local_time": "08:00"}
    ]
    for court in timing["courts"]:
        court["availability"] = [
            {"start_at": "2030-01-01T08:00:00+00:00", "end_at": "2030-01-01T13:00:00+00:00"}
        ]
    snapshot.config.update(timing)
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    source = Match(
        division_id=division.id,
        stage_id=stage.id,
        side_a=EntrySide(entry_id=snapshot.entries[0].id),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    target = Match(
        division_id=division.id,
        stage_id=stage.id,
        display_code="FINAL-7",
        side_a=EntrySide(entry_id=snapshot.entries[2].id),
        side_b=EntrySide(entry_id=snapshot.entries[3].id),
    )
    snapshot.matches = [source, target]
    choices = (
        [
            WinnerSide(match_id=source.id).model_dump(),
            EntrySide(entry_id=snapshot.entries[2].id).model_dump(),
        ]
        if sources
        else None
    )
    operation = declaration(snapshot, division, choices)
    record = propose_deferred_draw(snapshot, operation, "organizer", "onsite")
    approve(snapshot, record)
    bind_deferred_draw(
        snapshot,
        record["id"],
        [
            {"match_id": target.id, "side": "side_a", "slot_id": "1"},
            {"match_id": target.id, "side": "side_b", "slot_id": "2"},
        ],
    )
    return snapshot, source, target, record


def test_unresolved_slots_are_real_conservative_graph_and_solver_inputs():
    snapshot, source, target, _record = setup_draw(sources=True)
    assert validate_graph(snapshot) == []
    assert dependency_map(snapshot)[target.id] == {source.id}
    assert possible_participants(snapshot)[target.id] == {p.id for p in snapshot.participants[:3]}
    result = solve_schedule(snapshot)
    assert result["status"] in {"OPTIMAL", "FEASIBLE"}
    times = {a.match_id: a for a in snapshot.assignments}
    assert times[target.id].start_at >= times[source.id].play_end_at + timedelta(minutes=5)
    assert times[target.id].start_at >= datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
    assert validate_schedule(snapshot) == []
    assert (
        resolve_side(snapshot, next(m for m in snapshot.matches if m.id == target.id).side_a)
        is None
    )
    times[target.id].start_at = datetime(2030, 1, 1, 9, tzinfo=UTC)
    assert "DEFERRED_DRAW_NOT_READY" in {i.code for i in validate_schedule(snapshot)}


def test_approved_execution_rebinds_without_losing_identity_schedule_or_locks():
    snapshot, _source, target, parent = setup_draw()
    assert solve_schedule(snapshot)["status"] in {"OPTIMAL", "FEASIBLE"}
    assignments = deepcopy(snapshot.assignments)
    assignments[-1].locks = {"time": True, "court": True}
    snapshot.assignments = assignments
    original_assignments = deepcopy(assignments)
    performed = realize_deferred_draw(snapshot, execution(snapshot, parent), "organizer", "signed")
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError, match="approved"):
        bind_deferred_execution(snapshot, performed["id"])
    assert snapshot.model_dump(mode="json") == before
    approve(snapshot, performed)
    affected = bind_deferred_execution(snapshot, performed["id"])
    rebound = next(m for m in snapshot.matches if m.id == target.id)
    assert affected == [target.id]
    assert rebound.display_code == "FINAL-7"
    assert snapshot.assignments == original_assignments
    assert resolve_side(snapshot, rebound.side_a) == snapshot.entries[0].id
    assert possible_participants(snapshot)[target.id] == {p.id for p in snapshot.participants[:2]}
    assert validate_schedule(snapshot) == []


@pytest.mark.parametrize("protected", ["started", "sides", "results"])
def test_execution_rebind_rejects_protected_ancestry_atomically(protected):
    snapshot, _source, target, parent = setup_draw()
    performed = realize_deferred_draw(snapshot, execution(snapshot, parent), "organizer", "signed")
    approve(snapshot, performed)
    if protected == "started":
        next(m for m in snapshot.matches if m.id == target.id).status = "in_progress"
    elif protected == "results":
        snapshot.results.append({"match_id": target.id, "status": "draft"})
    else:
        assert solve_schedule(snapshot)["status"] in {"OPTIMAL", "FEASIBLE"}
        next(a for a in snapshot.assignments if a.match_id == target.id).locks = {"sides": True}
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError):
        bind_deferred_execution(snapshot, performed["id"])
    assert snapshot.model_dump(mode="json") == before


def test_stale_declaration_cannot_silently_remove_candidates():
    snapshot, _source, _target, _parent = setup_draw()
    snapshot.entries[0].status = "withdrawn"
    assert any(i.code == "DEFERRED_DRAW_INVALID" for i in validate_graph(snapshot))
    assert solve_schedule(snapshot)["status"] == "MODEL_INVALID"


def test_g3_preserves_approved_deferred_bindings_and_rejects_stale_evidence():
    from squash_scheduler.domain.draws import run_draw
    from squash_scheduler.domain.matches import generate_matches
    from squash_scheduler.domain.models import new_id

    snapshot, division = event(4, template="single_elimination")
    ordinary = run_draw(snapshot, division.id, 7, "organizer")
    approval_id = new_id()
    snapshot.approvals.append(
        {
            "id": approval_id,
            "target_type": "draw",
            "target_id": ordinary["id"],
            "basis_hash": target_basis(ordinary),
            "action": "approve",
        }
    )
    ordinary.update(status="approved", approval_id=approval_id)
    generate_matches(snapshot)
    record = propose_deferred_draw(snapshot, declaration(snapshot, division), "organizer", "onsite")
    approve(snapshot, record)
    first = snapshot.matches[0]
    bind_deferred_draw(
        snapshot,
        record["id"],
        [
            {"match_id": first.id, "side": "side_a", "slot_id": "1"},
            {"match_id": first.id, "side": "side_b", "slot_id": "2"},
        ],
    )
    before = snapshot.model_dump(mode="json")
    report = validate(snapshot, "G3")
    assert not [i for i in report["issues"] if i["gate"] == "G3"]
    assert snapshot.model_dump(mode="json") == before
    next(a for a in snapshot.approvals if a["id"] == record["approval_id"])["status"] = "stale"
    assert "DEFERRED_DRAW_INVALID" in {i["code"] for i in validate(snapshot, "G3")["issues"]}
    assert solve_schedule(snapshot)["status"] == "MODEL_INVALID"


def test_deferred_g3_checks_unbound_records_and_never_accepts_fake_approval():
    snapshot, division = event(3)
    record = propose_deferred_draw(snapshot, declaration(snapshot, division), "organizer", "onsite")
    record.update(status="approved", approval_id="fabricated")
    assert "DEFERRED_DRAW_INVALID" in {i["code"] for i in validate(snapshot, "G3")["issues"]}
