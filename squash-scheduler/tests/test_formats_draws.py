"""Synthetic acceptance coverage for AT-08 through AT-14 and graph integrity."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.formats import recommend_formats, validate_formats
from squash_scheduler.domain.matches import (
    dependency_map,
    elimination,
    generate_matches,
    possible_participants,
    round_robin,
    validate_graph,
)
from squash_scheduler.domain.models import (
    Assignment,
    Division,
    Entry,
    EntrySide,
    LoserSide,
    Match,
    Participant,
    Registration,
    Snapshot,
    Stage,
    WinnerSide,
    new_id,
    stable_id,
)


def event(count, template="round_robin"):
    division = Division(
        name="Synthetic",
        category="competitive",
        gender_category="open",
        status="active",
        format_plan={
            "template_id": template,
            "template_version": "1.0.0",
            "pool_sizes": [],
            "advancement_counts": [],
            "bracket_capacity": 1 << (count - 1).bit_length(),
            "placement_matches": [],
            "status": "approved",
            "approval_id": new_id(),
        },
    )
    snapshot = Snapshot(
        config={
            "draw_defaults": {
                "seeded_entries_mode": "none",
                "affiliation_avoidance_mode": "disabled",
            }
        },
        divisions=[division],
    )
    for i in range(count):
        person = Participant(official_name=f"Player {i}", display_name=f"Player {i}")
        registration = Registration(
            participant_id=person.id,
            original_division_id=division.id,
            source_ref={"synthetic": True},
        )
        entry = Entry(registration_id=registration.id, effective_division_id=division.id)
        snapshot.participants.append(person)
        snapshot.registrations.append(registration)
        snapshot.entries.append(entry)
    return snapshot, division


def approve_draw(record):
    record["status"] = "approved"
    record["approval_id"] = new_id()


def test_five_entry_complete_round_robin_and_stable_pair_identity():
    snapshot, division = event(5)
    generate_matches(snapshot)
    assert len(snapshot.matches) == 10
    appearances = {e.id: 0 for e in snapshot.entries}
    for match in snapshot.matches:
        appearances[match.side_a.entry_id] += 1
        appearances[match.side_b.entry_id] += 1
    assert set(appearances.values()) == {4}
    assert {
        m.id
        for m in round_robin(list(reversed(list(appearances))), division.id, snapshot.stages[0].id)
    } == {m.id for m in snapshot.matches}
    assert validate_graph(snapshot) == []


def test_approved_exclusion_survives_regeneration_and_missing_pair_is_error():
    snapshot, _ = event(5)
    generate_matches(snapshot)
    excluded = snapshot.matches[0]
    snapshot.exclusions.append(
        {
            "id": new_id(),
            "match_id": excluded.id,
            "reason": "Organizer approved synthetic omission",
            "approval_id": new_id(),
        }
    )
    generate_matches(snapshot)
    assert len(snapshot.matches) == 10
    assert sum(m.status != "excluded" for m in snapshot.matches) == 9
    assert any(i.code == "ROUND_ROBIN_INCOMPLETE" for i in validate_graph(snapshot))
    generate_matches(snapshot)
    assert next(m for m in snapshot.matches if m.id == excluded.id).status == "excluded"
    snapshot.matches.pop()
    assert any(i.code == "MATCH_PAIR_MISSING" for i in validate_graph(snapshot))


def test_six_entry_knockout_has_five_actual_matches_and_no_byes():
    snapshot, division = event(6, "single_elimination")
    division.draw_overrides = {"bye_allocation_rule": {"positions": ["2", "8"]}}
    approve_draw(run_draw(snapshot, division.id, 48, "tester"))
    generate_matches(snapshot)
    assert len(snapshot.matches) == 5
    assert [sum(m.round == r for m in snapshot.matches) for r in (1, 2, 3)] == [2, 2, 1]
    assert not any(s.type == "bye" for m in snapshot.matches for s in (m.side_a, m.side_b))
    assert validate_graph(snapshot) == []


def test_nine_pool_example_has_nine_prelims_five_knockout_and_bronze():
    snapshot, division = event(9, "pools_3x3_top2_knockout6_bronze")
    division.format_plan.update(
        pool_sizes=[3, 3, 3],
        advancement_counts=[2, 2, 2],
        bracket_capacity=8,
        placement_matches=["bronze"],
    )
    approve_draw(run_draw(snapshot, division.id, 77, "tester"))
    generate_matches(snapshot)
    assert len(snapshot.matches) == 15
    assert sum(m.side_a.type == m.side_b.type == "entry" for m in snapshot.matches) == 9
    bronze = next(m for m in snapshot.matches if m.kind == "placement")
    assert isinstance(bronze.side_a, LoserSide) and isinstance(bronze.side_b, LoserSide)
    graph = dependency_map(snapshot)
    assert len(graph[bronze.id]) == 2
    assert max(map(len, possible_participants(snapshot).values())) == 9
    assert validate_graph(snapshot) == []  # shared potential pool candidates are valid


def test_recommendations_do_not_choose_and_pool_advancement_is_explicit():
    snapshot, division = event(9)
    assert len(recommend_formats(snapshot)[division.id]["recommendations"]) == 4
    division.format_plan.update(
        template_id="pools_knockout",
        pool_sizes=[3, 3, 3],
        advancement_counts=[2],
        bracket_capacity=8,
    )
    assert any(i.code == "FORMAT_INCOMPATIBLE" for i in validate_formats(snapshot))


def test_draw_reproducibility_input_hash_redraw_reason_and_global_rng_isolation():
    snapshot, division = event(6, "single_elimination")
    division.draw_overrides = {"bye_allocation_rule": {"positions": ["2", "8"]}}
    random.seed(879)
    state = random.getstate()
    first = run_draw(snapshot, division.id, 1234, "tester")
    second = run_draw(snapshot, division.id, 1234, "tester", reason="Replay audit")
    assert first["input_hash"] == second["input_hash"]
    assert first["output"] == second["output"]
    assert random.getstate() == state
    with pytest.raises(ValueError, match="redraw requires"):
        run_draw(snapshot, division.id, 1234, "tester")
    division.draw_overrides["permitted_slots"] = {snapshot.entries[0].id: ["1"]}
    third = run_draw(snapshot, division.id, 1234, "tester", reason="Changed approved constraints")
    assert third["input_hash"] != first["input_hash"]
    assert (
        next(s for s in third["output"]["slots"] if s["slot_id"] == "1")["entry_id"]
        == snapshot.entries[0].id
    )


def test_hard_affiliation_constraints_bound_search_without_relaxing():
    snapshot, division = event(4, "single_elimination")
    affiliation = new_id()
    for p in snapshot.participants:
        p.affiliation_ids = [affiliation]
    division.draw_overrides = {
        "affiliation_avoidance_mode": "hard",
        "affiliation_avoidance_scope": "first_round",
        "max_attempts": 5,
    }
    record = run_draw(snapshot, division.id, 5, "tester")
    assert record["output"] is None
    assert record["search_status"] == "not_found_within_limit"
    assert record["attempts"] == 5
    division.draw_overrides["affiliation_avoidance_mode"] = "soft"
    soft = run_draw(snapshot, division.id, 5, "tester", reason="Approved policy change")
    assert soft["output"]["soft_cost"] == 2
    assert len(soft["output"]["soft_violations"]) == 2


def test_unknown_affiliations_not_grouped_and_fixed_seed_slots_are_hard():
    snapshot, division = event(4, "single_elimination")
    entry = snapshot.entries[0].id
    division.draw_overrides = {
        "seeded_entries_mode": "provided",
        "seeded_entries": [{"entry_id": entry, "seed_number": 1, "allowed_positions": ["1", "4"]}],
        "fixed_positions": {entry: "4"},
        "affiliation_avoidance_mode": "hard",
        "affiliation_avoidance_scope": "first_round",
    }
    record = run_draw(snapshot, division.id, 3, "tester")
    assert len(record["incomplete_affiliation_entry_ids"]) == 4
    assert record["output"]["slots"][3]["entry_id"] == entry
    division.draw_overrides["fixed_positions"][snapshot.entries[1].id] = "4"
    with pytest.raises(ValueError, match="Duplicate fixed"):
        run_draw(snapshot, division.id, 3, "tester", reason="Invalid synthetic input")


def test_graph_dangling_cycle_and_same_actual_person_across_entries():
    snapshot, division = event(4, "single_elimination")
    stage = Stage(division_id=division.id, type="elimination")
    snapshot.stages = [stage]
    matches = elimination([e.id for e in snapshot.entries], division.id, stage.id)
    snapshot.matches = matches
    first, last = matches[0], matches[-1]
    first.side_a = WinnerSide(match_id=last.id)
    assert any(i.code == "MATCH_GRAPH_CYCLE" for i in validate_graph(snapshot))
    first.side_a = WinnerSide(match_id=new_id())
    assert any(i.code == "MATCH_REFERENCE_INVALID" for i in validate_graph(snapshot))
    snapshot.registrations[1].participant_id = snapshot.registrations[0].participant_id
    first.side_a = EntrySide(entry_id=snapshot.entries[0].id)
    first.side_b = EntrySide(entry_id=snapshot.entries[1].id)
    assert any(i.code == "MATCH_SELF_PLAY" for i in validate_graph(snapshot))


def test_generation_requires_draw_approval_and_invalidates_changed_inputs():
    snapshot, division = event(4, "single_elimination")
    record = run_draw(snapshot, division.id, 0, "tester")
    with pytest.raises(ValueError, match="DRAW_APPROVAL_REQUIRED"):
        generate_matches(snapshot)
    approve_draw(record)
    snapshot.participants[0].affiliation_ids = [new_id()]
    with pytest.raises(ValueError, match="APPROVAL_STALE"):
        generate_matches(snapshot)


def redraw_with_swapped_slots(snapshot, division, first, second):
    entries = [slot["entry_id"] for slot in snapshot.draws[-1]["output"]["slots"]]
    entries[first], entries[second] = entries[second], entries[first]
    division.draw_overrides["fixed_positions"] = {
        entry: str(index + 1) for index, entry in enumerate(entries) if entry is not None
    }
    approve_draw(run_draw(snapshot, division.id, 19, "tester", reason="Reviewed replacement draw"))


def protect_match(snapshot, match, protection):
    if protection in {"in_progress", "completed"}:
        match.status = protection
    else:
        start = datetime(2026, 1, 1, 8, tzinfo=UTC)
        snapshot.assignments.append(
            Assignment(
                match_id=match.id,
                court_id=new_id(),
                start_at=start,
                play_end_at=start + timedelta(minutes=20),
                court_release_at=start + timedelta(minutes=25),
                locks={protection: True},
            )
        )


@pytest.mark.parametrize("protection", ["in_progress", "completed", "sides", "draw"])
def test_redraw_cannot_replace_started_or_locked_sides_and_failure_is_atomic(protection):
    snapshot, division = event(4, "single_elimination")
    approve_draw(run_draw(snapshot, division.id, 4, "tester"))
    generate_matches(snapshot)
    match = next(m for m in snapshot.matches if m.round == 1)
    protect_match(snapshot, match, protection)
    redraw_with_swapped_slots(snapshot, division, 0, 2)
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="MATCH_REGENERATION_LOCKED"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


@pytest.mark.parametrize("protection", ["completed", "sides", "draw"])
def test_changed_pool_membership_cannot_retarget_protected_final_with_same_candidate_union(
    protection,
):
    snapshot, division = event(9, "pools_3x3_top2_knockout6_bronze")
    division.format_plan.update(
        pool_sizes=[3, 3, 3],
        advancement_counts=[2, 2, 2],
        bracket_capacity=8,
        placement_matches=["bronze"],
    )
    approve_draw(run_draw(snapshot, division.id, 7, "tester"))
    generate_matches(snapshot)
    final = next(m for m in snapshot.matches if m.kind == "competitive" and m.round == 3)
    old_sides = (final.side_a.model_dump(), final.side_b.model_dump())
    old_candidates = possible_participants(snapshot)[final.id]
    protect_match(snapshot, final, protection)
    redraw_with_swapped_slots(snapshot, division, 0, 3)
    # The match ID, its direct winner references, and the total union all stay the same.
    unlocked = snapshot.model_copy(deep=True)
    next(m for m in unlocked.matches if m.id == final.id).status = "planned"
    unlocked.assignments = []
    generate_matches(unlocked)
    candidate_final = next(m for m in unlocked.matches if m.id == final.id)
    assert (candidate_final.side_a.model_dump(), candidate_final.side_b.model_dump()) == old_sides
    assert possible_participants(unlocked)[final.id] == old_candidates
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="MATCH_REGENERATION_LOCKED"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


@pytest.mark.parametrize("protection", ["completed", "sides"])
def test_regeneration_cannot_drop_started_or_locked_match(protection):
    snapshot, _ = event(5)
    generate_matches(snapshot)
    removed_entry = snapshot.entries[0]
    match = next(
        m for m in snapshot.matches if removed_entry.id in {m.side_a.entry_id, m.side_b.entry_id}
    )
    protect_match(snapshot, match, protection)
    removed_entry.status = "withdrawn"
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="MATCH_REGENERATION_LOCKED"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


@pytest.mark.parametrize("kind", ["placement", "exhibition"])
def test_regeneration_preserves_manual_match_and_its_stage(kind):
    snapshot, division = event(4)
    generate_matches(snapshot)
    stage = Stage(division_id=division.id, type="placement")
    manual = Match(
        division_id=division.id,
        stage_id=stage.id,
        kind=kind,
        side_a=EntrySide(entry_id=snapshot.entries[0].id),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    snapshot.stages.append(stage)
    snapshot.matches.append(manual)
    generate_matches(snapshot)
    assert next(m for m in snapshot.matches if m.id == manual.id) == manual
    assert any(s.id == stage.id for s in snapshot.stages)
    assert len(snapshot.matches) == 7
    assert validate_graph(snapshot) == []


def test_unchanged_protected_graph_can_be_regenerated():
    snapshot, division = event(4, "single_elimination")
    approve_draw(run_draw(snapshot, division.id, 4, "tester"))
    generate_matches(snapshot)
    protect_match(snapshot, snapshot.matches[0], "completed")
    protect_match(snapshot, snapshot.matches[-1], "draw")
    before = snapshot.model_dump()
    generate_matches(snapshot)
    assert snapshot.model_dump() == before


def explicit_placement(division, label="Fifth place", source_round=1):
    main_stage = stable_id("stage", division.id, "elimination")
    return {
        "id": new_id(),
        "label": label,
        "side_a": {
            "type": "match_loser",
            "match_id": stable_id("elimination", main_stage, str(source_round), "0"),
        },
        "side_b": {
            "type": "match_loser",
            "match_id": stable_id("elimination", main_stage, str(source_round), "1"),
        },
        "round": 1,
        "bracket_position": 0,
    }


def explicit_event(count=8, definition=None):
    snapshot, division = event(count, "single_elimination")
    definition = definition or explicit_placement(division)
    division.format_plan["placement_matches"] = [definition]
    approve_draw(run_draw(snapshot, division.id, 22, "tester"))
    generate_matches(snapshot)
    return snapshot, division, definition


def test_explicit_semifinal_losers_placement_has_own_stage_and_defined_label():
    snapshot, division = event(4, "single_elimination")
    definition = explicit_placement(division, "Bronze medal playoff")
    definition["code"] = "BRONZE-EXPLICIT"
    division.format_plan["placement_matches"] = [definition]
    approve_draw(run_draw(snapshot, division.id, 22, "tester"))
    generate_matches(snapshot)
    placement = next(m for m in snapshot.matches if m.id == definition["id"])
    assert len(snapshot.matches) == 4 and placement.kind == "placement"
    assert placement.stage_id == stable_id("stage", division.id, "placement")
    assert next(s for s in snapshot.stages if s.id == placement.stage_id).type == "placement"
    assert placement.display_code == "BRONZE-EXPLICIT"
    assert dependency_map(snapshot)[placement.id] == {
        definition["side_a"]["match_id"],
        definition["side_b"]["match_id"],
    }
    assert validate_graph(snapshot) == []


def test_arbitrary_fifth_place_definition_is_explicit_and_regeneration_preserves_manual_matches():
    snapshot, division, definition = explicit_event()
    placement = next(m for m in snapshot.matches if m.id == definition["id"])
    assert len(snapshot.matches) == 8 and placement.display_code == "Fifth place"
    manual_stage = Stage(division_id=division.id, type="placement")
    manual = Match(
        division_id=division.id,
        stage_id=manual_stage.id,
        kind="exhibition",
        side_a=EntrySide(entry_id=snapshot.entries[0].id),
        side_b=EntrySide(entry_id=snapshot.entries[1].id),
    )
    snapshot.stages.append(manual_stage)
    snapshot.matches.append(manual)
    generate_matches(snapshot)
    assert next(m for m in snapshot.matches if m.id == manual.id) == manual
    assert any(s.id == manual_stage.id for s in snapshot.stages)
    assert sum(m.id == placement.id for m in snapshot.matches) == 1
    assert validate_graph(snapshot) == []


@pytest.mark.parametrize(
    "invalid",
    [
        "unknown_label",
        "invalid_uuid",
        "unknown_side_type",
        "boolean_round",
        "negative_position",
        "missing_label",
        "duplicate_id",
        "duplicate_bronze",
    ],
)
def test_malformed_placement_definition_blocks_generation_without_mutation(invalid):
    snapshot, division = event(8, "single_elimination")
    definition = explicit_placement(division)
    placement = [definition]
    if invalid == "unknown_label":
        placement = ["encouragement"]
    elif invalid == "invalid_uuid":
        definition["id"] = "rank-five"
    elif invalid == "unknown_side_type":
        definition["side_a"] = {"type": "unknown", "name": "Fifth qualifier"}
    elif invalid == "boolean_round":
        definition["round"] = True
    elif invalid == "negative_position":
        definition["bracket_position"] = -1
    elif invalid == "missing_label":
        definition.pop("label")
    elif invalid == "duplicate_id":
        placement.append(dict(definition))
    else:
        placement = ["bronze", "bronze"]
    division.format_plan["placement_matches"] = placement
    before = snapshot.model_dump()
    assert any(i.code == "FORMAT_INCOMPATIBLE" for i in validate_formats(snapshot))
    with pytest.raises(ValueError):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


@pytest.mark.parametrize(
    "reference", ["missing", "self_cycle", "cross_division", "placement_cycle"]
)
def test_explicit_placement_references_must_belong_to_generated_main_draw(reference):
    snapshot, division = event(8, "single_elimination")
    definition = explicit_placement(division)
    placements = [definition]
    if reference == "missing":
        definition["side_a"]["match_id"] = new_id()
    elif reference == "self_cycle":
        definition["side_a"]["match_id"] = definition["id"]
    elif reference == "cross_division":
        other, other_division = event(4, "single_elimination")
        approve_draw(run_draw(other, other_division.id, 8, "tester"))
        generate_matches(other)
        snapshot.divisions.extend(other.divisions)
        snapshot.participants.extend(other.participants)
        snapshot.registrations.extend(other.registrations)
        snapshot.entries.extend(other.entries)
        snapshot.draws.extend(other.draws)
        definition["side_a"]["match_id"] = other.matches[0].id
    else:
        second = explicit_placement(division, "Sixth place")
        definition["side_a"]["match_id"] = second["id"]
        second["side_a"]["match_id"] = definition["id"]
        placements.append(second)
    division.format_plan["placement_matches"] = placements
    approve_draw(run_draw(snapshot, division.id, 4, "tester"))
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="PLACEMENT_REFERENCE_INVALID"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


@pytest.mark.parametrize("protection", ["completed", "sides", "draw"])
def test_editing_explicit_placement_keeps_started_and_locked_qualifications(protection):
    snapshot, division, definition = explicit_event()
    placement = next(m for m in snapshot.matches if m.id == definition["id"])
    protect_match(snapshot, placement, protection)
    stage = stable_id("stage", division.id, "elimination")
    definition["side_a"]["match_id"] = stable_id("elimination", stage, "1", "2")
    approve_draw(run_draw(snapshot, division.id, 22, "tester", reason="Review changed placement"))
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="MATCH_REGENERATION_LOCKED"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


def test_unknown_encouragement_gets_missing_definition_recommendation_without_inference():
    snapshot, division = event(8)
    division.format_plan["placement_matches"] = ["encouragement"]
    choices = recommend_formats(snapshot)[division.id]["recommendations"]
    assert all("explicit placement definition for encouragement" in c["missing"] for c in choices)
    assert snapshot.matches == []
