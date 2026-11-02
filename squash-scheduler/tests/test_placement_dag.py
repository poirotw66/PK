"""Approved explicit placement forests preserve typed topology and historical identity."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_formats_draws import approve_draw, event, explicit_placement, protect_match

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.formats import validate_formats
from squash_scheduler.domain.matches import dependency_map, generate_matches, validate_graph
from squash_scheduler.domain.models import Snapshot, canonical_hash, content_hash, new_id, stable_id
from squash_scheduler.storage.repository import Repository


def placement_tree():
    snapshot, division = event(8, "single_elimination")
    first = explicit_placement(division, "C1")
    second = explicit_placement(division, "C2")
    main_stage = stable_id("stage", division.id, "elimination")
    second["side_a"]["match_id"] = stable_id("elimination", main_stage, "1", "2")
    second["side_b"]["match_id"] = stable_id("elimination", main_stage, "1", "3")
    final = explicit_placement(division, "CF")
    final.update(
        round=2,
        side_a={"type": "match_winner", "match_id": first["id"]},
        side_b={"type": "match_winner", "match_id": second["id"]},
    )
    for definition in (first, second, final):
        definition.update(stage_key="additional_placement", stage_label="附加名次賽")
    division.format_plan["placement_matches"] = [final, second, first]
    approve_draw(run_draw(snapshot, division.id, 22, "tester"))
    return snapshot, division, first, second, final


def refresh(snapshot, division):
    approve_draw(run_draw(snapshot, division.id, 22, "tester", reason="Reviewed typed topology"))
    generate_matches(snapshot)


def test_forward_tree_is_placement_and_regenerates_idempotently():
    snapshot, division, first, second, final = placement_tree()
    generate_matches(snapshot)
    assert len(snapshot.matches) == 10
    stage = next(s for s in snapshot.stages if s.stage_key == "additional_placement")
    assert stage.id == stable_id("placement-stage", division.id, "additional_placement")
    assert stage.label == "附加名次賽"
    assert all(m.kind == "placement" for m in snapshot.matches if m.stage_id == stage.id)
    assert dependency_map(snapshot)[final["id"]] == {first["id"], second["id"]}
    assert validate_graph(snapshot) == []
    before = snapshot.model_dump()
    generate_matches(snapshot)
    assert snapshot.model_dump() == before


def test_same_division_forward_references_can_cross_stage_keys():
    snapshot, division, first, second, final = placement_tree()
    final.update(stage_key="final", stage_label="Encouragement final")
    refresh(snapshot, division)
    matches = {match.id: match for match in snapshot.matches}
    assert matches[final["id"]].stage_id != matches[first["id"]].stage_id
    assert dependency_map(snapshot)[final["id"]] == {first["id"], second["id"]}
    assert validate_graph(snapshot) == []


@pytest.mark.parametrize("invalid", ["self", "cycle", "missing", "foreign"])
def test_bad_forward_graph_is_atomic(invalid):
    snapshot, division, first, _, final = placement_tree()
    if invalid == "self":
        first["side_a"]["match_id"] = first["id"]
    elif invalid == "cycle":
        first["side_a"]["match_id"] = final["id"]
    elif invalid == "missing":
        final["side_a"]["match_id"] = new_id()
    else:
        other, other_division = event(4, "single_elimination")
        foreign = explicit_placement(other_division)
        other_division.format_plan["placement_matches"] = [foreign]
        approve_draw(run_draw(other, other_division.id, 22, "tester"))
        for field in ("divisions", "participants", "registrations", "entries", "draws"):
            getattr(snapshot, field).extend(getattr(other, field))
        final["side_a"]["match_id"] = foreign["id"]
    approve_draw(run_draw(snapshot, division.id, 22, "tester", reason="Invalid trial"))
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="PLACEMENT_REFERENCE_INVALID"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


@pytest.mark.parametrize("field,value", [("stage_key", ""), ("stage_label", None)])
def test_stage_metadata_validation_is_g2(field, value):
    snapshot, _, first, _, _ = placement_tree()
    first[field] = value
    assert any(i.code == "FORMAT_INCOMPATIBLE" for i in validate_formats(snapshot))


def test_conflicting_labels_rejected_and_legacy_label_allowed():
    snapshot, division, first, second, _ = placement_tree()
    first["stage_label"] = "Other label"
    assert any("labels conflict" in i.message for i in validate_formats(snapshot))
    division.format_plan["placement_matches"] = [second]
    second.pop("stage_key")
    refresh(snapshot, division)
    stage = next(s for s in snapshot.stages if s.type == "placement")
    assert stage.id == stable_id("stage", division.id, "placement")
    assert stage.label == "附加名次賽" and stage.stage_key is None


@pytest.mark.parametrize("protection", ["completed", "sides", "draw"])
@pytest.mark.parametrize("change", ["ancestry", "remove", "move_key"])
def test_protected_tree_cannot_be_retargeted_or_removed(protection, change):
    snapshot, division, first, _, final = placement_tree()
    generate_matches(snapshot)
    protect_match(snapshot, next(m for m in snapshot.matches if m.id == final["id"]), protection)
    if change == "ancestry":
        first["side_a"], first["side_b"] = first["side_b"], first["side_a"]
    elif change == "remove":
        division.format_plan["placement_matches"].remove(final)
    else:
        final.update(stage_key="other-key", stage_label="Other stage")
    approve_draw(run_draw(snapshot, division.id, 22, "tester", reason="Protected trial"))
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="MATCH_REGENERATION_LOCKED"):
        generate_matches(snapshot)
    assert snapshot.model_dump() == before


def test_other_stage_key_and_other_division_locks_survive_metadata_and_order_changes():
    snapshot, division, first, _, final = placement_tree()
    other, other_division = event(4, "single_elimination")
    approve_draw(run_draw(other, other_division.id, 22, "tester"))
    generate_matches(other)
    for field in (
        "divisions",
        "participants",
        "registrations",
        "entries",
        "draws",
        "stages",
        "matches",
    ):
        getattr(snapshot, field).extend(getattr(other, field))
    generate_matches(snapshot)
    protect_match(snapshot, next(m for m in snapshot.matches if m.id == final["id"]), "draw")
    protect_match(
        snapshot,
        next(m for m in snapshot.matches if m.division_id == other_division.id),
        "completed",
    )
    extra = deepcopy(first)
    extra.update(id=new_id(), stage_key="aaa", stage_label="Other additional_placement")
    division.format_plan["placement_matches"].append(extra)
    for definition in division.format_plan["placement_matches"]:
        if definition.get("stage_key") == "additional_placement":
            definition["stage_label"] = "Renamed display label"
    refresh(snapshot, division)
    assert (
        next(s for s in snapshot.stages if s.stage_key == "additional_placement").label
        == "Renamed display label"
    )
    division.format_plan["placement_matches"].remove(extra)
    refresh(snapshot, division)
    assert not any(s.stage_key == "aaa" for s in snapshot.stages)
    assert not any(m.id == extra["id"] for m in snapshot.matches)


def test_stage_optional_metadata_preserves_legacy_envelope_hash_and_approvals(tmp_path):
    snapshot, _ = event(4)
    generate_matches(snapshot)
    snapshot.approvals = [{"id": new_id(), "content_hash": content_hash(snapshot)}]
    legacy = snapshot.model_dump(mode="json")
    for stage in legacy["stages"]:
        stage.pop("label", None)
        stage.pop("stage_key", None)
    payload = deepcopy(legacy)
    for key in ("id", "parent_id", "status", "created_at", "approvals", "issues", "changes"):
        payload.pop(key, None)
    envelope = {
        "snapshot": legacy,
        "content_hash": canonical_hash(payload),
        "snapshot_hash": canonical_hash(legacy),
    }
    working = tmp_path / "working"
    working.mkdir()
    (working / "current.json").write_text(json.dumps(envelope), encoding="utf-8")
    loaded = Repository(tmp_path).load()
    assert loaded.model_dump(mode="json") == legacy
    assert loaded.approvals[0]["content_hash"] == content_hash(loaded)
    schema = json.loads(Path("schemas/snapshot.schema.json").read_text("utf-8"))
    assert schema == Snapshot.model_json_schema()
