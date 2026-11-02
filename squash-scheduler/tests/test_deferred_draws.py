"""Future draw declarations retain complete candidate and execution evidence."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_formats_draws import event

from squash_scheduler.domain.deferred_draws import (
    input_basis,
    propose_deferred_draw,
    realize_deferred_draw,
    slot_candidates,
    slot_dependencies,
    slot_duration_minutes,
    slot_ready_at,
    validate_deferred_draw,
)
from squash_scheduler.domain.models import (
    Assignment,
    DrawSlotSide,
    EntrySide,
    Match,
    Pool,
    PoolRankSide,
    Stage,
    WinnerSide,
    new_id,
)
from squash_scheduler.domain.validation import target_basis


def approve(snapshot, record):
    approval_id = new_id()
    snapshot.approvals.append(
        {
            "id": approval_id,
            "target_type": "deferred_draw",
            "target_id": record["id"],
            "basis_hash": target_basis(record),
            "action": "approve",
            "actor_id": "reviewer",
        }
    )
    record.update(status="approved", approval_id=approval_id)


def declaration(snapshot, division, sources=None):
    sources = sources or [EntrySide(entry_id=e.id).model_dump() for e in snapshot.entries]
    return {
        "division_id": division.id,
        "slots": [
            {"slot_id": str(i + 1), "candidate_sides": deepcopy(sources)}
            for i in range(len(sources))
        ],
        "procedure": {
            "description": "Organizer draws all confirmed qualifiers from a bag",
            "execution_trigger": "after_sources_confirmed",
            "duration_minutes": 5,
            "earliest_at": "2030-01-01T09:00:00+00:00",
        },
    }


def execution(snapshot, record):
    return {
        "draw_run_id": record["id"],
        "assignments": [
            {"slot_id": str(i + 1), "entry_id": e.id} for i, e in enumerate(snapshot.entries)
        ],
        "executed_at": "2030-01-01T09:05:00+00:00",
        "evidence": "Signed draw sheet 7",
    }


def test_known_candidates_require_actual_approval_and_retain_full_sets():
    snapshot, division = event(3)
    record = propose_deferred_draw(snapshot, declaration(snapshot, division), "organizer", "plan")
    assert record["allowed_positions"] == {"mode": "disabled", "constraints": []}
    side = DrawSlotSide(draw_run_id=record["id"], slot_id="1")
    with pytest.raises(ValueError, match="approved"):
        slot_candidates(snapshot, side)
    approve(snapshot, record)
    assert validate_deferred_draw(snapshot, record) == []
    assert slot_candidates(snapshot, side) == {p.id for p in snapshot.participants}
    assert slot_dependencies(snapshot, side) == set()
    assert slot_duration_minutes(snapshot, side) == 5
    assert slot_ready_at(snapshot, side) == datetime(2030, 1, 1, 9, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda op: op["procedure"].pop("duration_minutes"),
        lambda op: op["procedure"].update(duration_minutes=True),
        lambda op: op["procedure"].update(earliest_at="2030-01-01T09:00:00"),
        lambda op: op["slots"][0].update(candidate_sides=[{"type": "unknown", "name": "winner"}]),
        lambda op: op["slots"][0].update(candidate_sides=[{"type": "bye"}]),
        lambda op: op["slots"][0].update(
            candidate_sides=[
                {
                    "type": "draw_slot",
                    "draw_run_id": new_id(),
                    "slot_id": "1",
                }
            ]
        ),
        lambda op: op["slots"][0].update(
            candidate_sides=[
                {
                    "type": "match_winner",
                    "match_id": new_id(),
                }
            ]
        ),
        lambda op: op["slots"][1].update(slot_id="1"),
    ],
)
def test_malformed_declarations_are_atomic(mutation):
    snapshot, division = event(3)
    operation = declaration(snapshot, division)
    mutation(operation)
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError):
        propose_deferred_draw(snapshot, operation, "organizer", "plan")
    assert snapshot.model_dump(mode="json") == before


def test_stale_and_fabricated_approvals_and_changed_sources_are_rejected():
    snapshot, division = event(3)
    record = propose_deferred_draw(snapshot, declaration(snapshot, division), "organizer", "plan")
    record.update(status="approved", approval_id=new_id())
    assert validate_deferred_draw(snapshot, record)
    approve(snapshot, record)
    snapshot.approvals[-1]["status"] = "stale"
    assert validate_deferred_draw(snapshot, record)
    snapshot.approvals[-1].pop("status")
    snapshot.entries[0].status = "withdrawn"
    assert validate_deferred_draw(snapshot, record)


def test_execution_retains_original_and_rejects_impossible_or_early_assignment():
    snapshot, division = event(3)
    record = propose_deferred_draw(snapshot, declaration(snapshot, division), "organizer", "plan")
    approve(snapshot, record)
    original = deepcopy(record)
    operation = execution(snapshot, record)
    operation["assignments"][1]["entry_id"] = operation["assignments"][0]["entry_id"]
    with pytest.raises(ValueError, match="distinct"):
        realize_deferred_draw(snapshot, operation, "organizer", "executed")
    operation = execution(snapshot, record)
    operation["executed_at"] = "2030-01-01T09:04:00+00:00"
    with pytest.raises(ValueError, match="timing"):
        realize_deferred_draw(snapshot, operation, "organizer", "executed")
    execution_record = realize_deferred_draw(
        snapshot, execution(snapshot, record), "organizer", "executed"
    )
    assert record == original
    assert execution_record["id"] != record["id"]
    assert execution_record["parent_draw_run_id"] == record["id"]
    assert execution_record["status"] == "draft"
    assert validate_deferred_draw(snapshot, execution_record) == []
    approve(snapshot, execution_record)
    side = DrawSlotSide(draw_run_id=execution_record["id"], slot_id="1")
    assert slot_candidates(snapshot, side) == {snapshot.participants[0].id}
    assert slot_ready_at(snapshot, side) == datetime(2030, 1, 1, 9, 5, tzinfo=UTC)


def test_position_constraints_are_enforced_for_actual_qualification():
    snapshot, division = event(3)
    operation = declaration(snapshot, division)
    operation["allowed_positions"] = {
        "mode": "provided",
        "constraints": [
            {
                "candidate_side": EntrySide(entry_id=snapshot.entries[0].id).model_dump(),
                "slot_ids": ["2"],
            }
        ],
    }
    record = propose_deferred_draw(snapshot, operation, "organizer", "restricted draw")
    approve(snapshot, record)
    with pytest.raises(ValueError, match="allowed"):
        realize_deferred_draw(snapshot, execution(snapshot, record), "organizer", "executed")
    op = execution(snapshot, record)
    op["assignments"][0]["entry_id"], op["assignments"][1]["entry_id"] = (
        op["assignments"][1]["entry_id"],
        op["assignments"][0]["entry_id"],
    )
    realize_deferred_draw(snapshot, op, "organizer", "executed")


def test_impossible_declared_positions_are_rejected_before_approval():
    snapshot, division = event(3)
    operation = declaration(snapshot, division)
    operation["allowed_positions"] = {
        "mode": "provided",
        "constraints": [
            {"candidate_side": side, "slot_ids": ["1"]}
            for side in operation["slots"][0]["candidate_sides"]
        ],
    }
    with pytest.raises(ValueError, match="cannot fill"):
        propose_deferred_draw(snapshot, operation, "organizer", "plan")
    assert snapshot.draws == []


def test_known_ordinary_draw_slots_are_valid_source_ancestry():
    from squash_scheduler.domain.draws import draw_inputs
    from squash_scheduler.domain.models import canonical_hash

    snapshot, division = event(3)
    ordinary = {
        "id": new_id(),
        "division_id": division.id,
        "random_seed": 7,
        "input_hash": canonical_hash(draw_inputs(snapshot, division.id, 7)),
        "output": {"slots": [{"slot_id": "1", "entry_id": snapshot.entries[0].id}]},
        "status": "draft",
        "approval_id": None,
    }
    snapshot.draws.append(ordinary)
    ordinary["approval_id"] = new_id()
    ordinary["status"] = "approved"
    snapshot.approvals.append(
        {
            "id": ordinary["approval_id"],
            "target_type": "draw",
            "target_id": ordinary["id"],
            "basis_hash": target_basis(ordinary),
            "action": "approve",
        }
    )
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    source = Match(
        division_id=division.id,
        stage_id=stage.id,
        side_a=DrawSlotSide(draw_run_id=ordinary["id"], slot_id="1"),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    snapshot.matches.append(source)
    record = propose_deferred_draw(
        snapshot,
        declaration(snapshot, division, [WinnerSide(match_id=source.id).model_dump()]),
        "organizer",
        "plan",
    )
    approve(snapshot, record)
    assert slot_candidates(snapshot, DrawSlotSide(draw_run_id=record["id"], slot_id="1")) == {
        person.id for person in snapshot.participants[:2]
    }


def test_source_cycle_and_cross_division_source_are_rejected():
    snapshot, division = event(3)
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    match = Match(
        division_id=division.id,
        stage_id=stage.id,
        side_a=EntrySide(entry_id=snapshot.entries[0].id),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    snapshot.matches.append(match)
    match.side_a = WinnerSide(match_id=match.id)
    operation = declaration(snapshot, division, [WinnerSide(match_id=match.id).model_dump()])
    with pytest.raises(ValueError, match="cycle"):
        propose_deferred_draw(snapshot, operation, "organizer", "plan")
    match.side_a = EntrySide(entry_id=snapshot.entries[0].id)
    match.division_id = new_id()
    with pytest.raises(ValueError, match="division"):
        propose_deferred_draw(snapshot, operation, "organizer", "plan")


def test_winner_source_progress_preserves_hash_and_has_dependency_readiness():
    snapshot, division = event(3)
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    match = Match(
        division_id=division.id,
        stage_id=stage.id,
        side_a=EntrySide(entry_id=snapshot.entries[0].id),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    snapshot.matches.append(match)
    sources = [WinnerSide(match_id=match.id).model_dump()]
    record = propose_deferred_draw(snapshot, declaration(snapshot, division, sources), "a", "r")
    approve(snapshot, record)
    side = DrawSlotSide(draw_run_id=record["id"], slot_id="1")
    assert slot_dependencies(snapshot, side) == {match.id}
    assert slot_candidates(snapshot, side) == {p.id for p in snapshot.participants[:2]}
    assert slot_ready_at(snapshot, side) is None
    start = datetime(2030, 1, 1, 9, tzinfo=UTC)
    snapshot.assignments.append(
        Assignment(
            match_id=match.id,
            court_id=new_id(),
            start_at=start,
            play_end_at=start + timedelta(minutes=25),
            court_release_at=start + timedelta(minutes=30),
        )
    )
    assert slot_ready_at(snapshot, side) == start + timedelta(minutes=30)
    snapshot.results.append(
        {
            "match_id": match.id,
            "status": "confirmed",
            "outcome": "played",
            "winner_id": snapshot.entries[0].id,
        }
    )
    match.status = "completed"
    assert input_basis(snapshot, record) == record["input_hash"]
    assert validate_deferred_draw(snapshot, record) == []


def test_source_result_must_be_confirmed_before_execution_and_never_guessed():
    snapshot, division = event(3)
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages.append(stage)
    match = Match(
        division_id=division.id,
        stage_id=stage.id,
        side_a=EntrySide(entry_id=snapshot.entries[0].id),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    snapshot.matches.append(match)
    operation = declaration(snapshot, division, [WinnerSide(match_id=match.id).model_dump()])
    operation["procedure"]["earliest_at"] = None
    record = propose_deferred_draw(snapshot, operation, "organizer", "plan")
    approve(snapshot, record)
    op = execution(snapshot, record)
    op["assignments"] = op["assignments"][:1]
    with pytest.raises(ValueError, match="confirmed"):
        realize_deferred_draw(snapshot, op, "organizer", "execute")
    snapshot.results.append(
        {
            "match_id": match.id,
            "status": "confirmed",
            "outcome": "played",
            "winner_id": snapshot.entries[0].id,
            "created_at": "2030-01-01T09:10:00+00:00",
        }
    )
    with pytest.raises(ValueError, match="confirmation"):
        realize_deferred_draw(snapshot, op, "organizer", "execute")
    op["executed_at"] = "2030-01-01T09:15:00+00:00"
    realized = realize_deferred_draw(snapshot, op, "organizer", "execute")
    assert realized["output"]["slots"][0]["entry_id"] == snapshot.entries[0].id


def test_pool_dependencies_cover_all_matches_and_confirmation_buffer():
    from squash_scheduler.domain.matches import round_robin

    snapshot, division = event(3)
    snapshot.config["timing_defaults"] = {"ranking_confirmation_minutes": 4}
    stage = Stage(division_id=division.id, type="round_robin")
    snapshot.stages.append(stage)
    pool = Pool(
        stage_id=stage.id,
        label="A",
        entry_ids=[e.id for e in snapshot.entries],
        advancement_count=1,
    )
    snapshot.pools.append(pool)
    snapshot.matches = round_robin(pool.entry_ids, division.id, stage.id)
    record = propose_deferred_draw(
        snapshot,
        declaration(snapshot, division, [PoolRankSide(pool_id=pool.id, rank=1).model_dump()]),
        "organizer",
        "plan",
    )
    approve(snapshot, record)
    start = datetime(2030, 1, 1, 9, tzinfo=UTC)
    for index, match in enumerate(snapshot.matches):
        snapshot.assignments.append(
            Assignment(
                match_id=match.id,
                court_id=new_id(),
                start_at=start + timedelta(minutes=30 * index),
                play_end_at=start + timedelta(minutes=30 * index + 25),
                court_release_at=start + timedelta(minutes=30 * index + 30),
            )
        )
    side = DrawSlotSide(draw_run_id=record["id"], slot_id="1")
    assert slot_dependencies(snapshot, side) == {m.id for m in snapshot.matches}
    assert slot_ready_at(snapshot, side) == start + timedelta(minutes=94)


def test_unconfirmed_pool_qualification_cannot_be_executed():
    snapshot, division = event(3)
    stage = Stage(division_id=division.id, type="round_robin")
    snapshot.stages.append(stage)
    pool = Pool(
        stage_id=stage.id,
        label="A",
        entry_ids=[e.id for e in snapshot.entries],
        advancement_count=1,
    )
    snapshot.pools.append(pool)
    record = propose_deferred_draw(
        snapshot,
        declaration(snapshot, division, [PoolRankSide(pool_id=pool.id, rank=1).model_dump()]),
        "organizer",
        "plan",
    )
    approve(snapshot, record)
    op = execution(snapshot, record)
    op["assignments"] = op["assignments"][:1]
    with pytest.raises(ValueError, match="confirmed"):
        realize_deferred_draw(snapshot, op, "organizer", "execute")
