"""Operator previews reuse domain rules and confirm against the exact base revision."""

import importlib.util
from pathlib import Path

import pytest
from test_encouragement_draws import qualified
from test_rankings import result

from squash_scheduler.domain.encouragement_draws import validate_encouragement
from squash_scheduler.domain.models import new_id
from squash_scheduler.operations import Conflict, OperatorService
from squash_scheduler.storage.repository import Repository


def operator_snapshot():
    snapshot, division_id = qualified()
    for match in snapshot.matches:
        match.status = "planned"
    snapshot.results = []
    source_id = new_id()
    snapshot.sources.append({"id": source_id, "synthetic_only": True})
    for registration in snapshot.registrations:
        registration.source_ref["source_file_id"] = source_id
    script = Path(__file__).parents[1] / "examples/build_consolation_demo.py"
    spec = importlib.util.spec_from_file_location("operator_config", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot.config = {**module.synthetic_config(), **snapshot.config}
    snapshot.config["divisions"] = [d.model_dump(mode="json") for d in snapshot.divisions]
    return snapshot, division_id


@pytest.fixture
def service(tmp_path):
    snapshot, _ = operator_snapshot()
    repo = Repository(tmp_path / "event")
    with repo.lock():
        repo.save(snapshot, None)
    operator = OperatorService(repo)
    yield operator
    operator.close()


def preview(service, **operation):
    return service.preview(
        {
            "revision_id": service.repo.load().id,
            "actor": "Justin",
            "reason": "Reviewed fixture operation",
            **operation,
        }
    )


def confirm(service, plan):
    return service.confirm({"id": plan["id"], "hash": plan["hash"]})


def test_preview_does_not_save_and_confirmation_is_exact_and_single_use(service):
    before = service.repo.load()
    division = before.divisions[0]
    plan = preview(service, action="seeds", division_id=division.id, mode="none", seeds=[])
    assert service.repo.load().id == before.id
    assert not service.repo.load().changes
    with pytest.raises(Conflict):
        confirm(service, {**plan, "hash": "wrong"})
    result = confirm(service, plan)
    assert result["revision_id"] != before.id
    assert service.repo.load().changes[-1]["actor_id"] == "Justin"
    with pytest.raises(Conflict):
        confirm(service, plan)


def test_new_revision_invalidates_previous_preview(service):
    division_id = service.repo.load().divisions[0].id
    one = preview(service, action="seeds", division_id=division_id, mode="none", seeds=[])
    two = preview(service, action="seeds", division_id=division_id, mode="none", seeds=[])
    confirm(service, two)
    with pytest.raises(Conflict, match="更新"):
        confirm(service, one)


def test_seed_edit_redraw_approval_and_schedule_use_existing_rules(service):
    before = service.repo.load()
    division = before.divisions[0]
    entry = before.entries[0]
    confirm(
        service,
        preview(
            service,
            action="seeds",
            division_id=division.id,
            mode="provided",
            seeds=[{"entry_id": entry.id, "seed_number": 1, "allowed_positions": ["1"]}],
        ),
    )
    assert service.repo.load().draws[-1]["status"] == "superseded"
    confirm(service, preview(service, action="draw", division_id=division.id, seed=242))
    draw = service.repo.load().draws[-1]
    confirm(service, preview(service, action="approve_draw", draw_id=draw["id"]))
    assert not service.repo.load().assignments
    plan = preview(service, action="schedule")
    proposed = plan["summary"]["assignments"]
    assert not service.repo.load().assignments
    confirm(service, plan)
    assert [a.model_dump(mode="json") for a in service.repo.load().assignments] == proposed


def test_results_and_qualified_encouragement_can_be_completed_without_cli(service):
    snapshot = service.repo.load()
    first = [m for m in snapshot.matches if m.round == 1]
    confirm(service, preview(service, action="results", results=[result(m) for m in first]))
    state = service.state()
    assert len(state["eligibility"][0]["eligible_entry_ids"]) == 4
    assert all(
        r["reason"] in {"FIRST_ROUND_WINNER", "FIRST_ROUND_LOSER"}
        for r in state["eligibility"][0]["entries"]
    )
    plan = preview(service, action="encouragement", division_id=snapshot.divisions[0].id, seed=99)
    assert len(service.repo.load().matches) == 7
    confirm(service, plan)
    assert len(service.repo.load().matches) == 10
    assert not validate_encouragement(service.repo.load())
    with pytest.raises(ValueError, match="已有開打"):
        preview(
            service, action="seeds", division_id=snapshot.divisions[0].id, mode="none", seeds=[]
        )


def test_invalid_score_and_seed_position_never_write(service):
    snapshot = service.repo.load()
    bad = result(snapshot.matches[0])
    bad["games"] = [{"side_a": 11, "side_b": 10}] * 2
    with pytest.raises(ValueError):
        preview(service, action="results", results=[bad])
    with pytest.raises(ValueError):
        preview(
            service,
            action="seeds",
            division_id=snapshot.divisions[0].id,
            mode="provided",
            seeds=[
                {"entry_id": snapshot.entries[0].id, "seed_number": 1, "allowed_positions": ["99"]}
            ],
        )
    assert service.repo.load().id == snapshot.id


def test_extra_request_fields_cannot_supply_unreviewed_assignments(service):
    with pytest.raises(ValueError, match="欄位"):
        preview(service, action="schedule", assignments=[])


def test_unchanged_seed_setting_preserves_approved_draw_and_assignments(service):
    before = service.repo.load()
    confirm(
        service,
        preview(service, action="seeds", division_id=before.divisions[0].id, mode="none", seeds=[]),
    )
    after = service.repo.load()
    assert after.draws == before.draws
    assert after.assignments == before.assignments


def test_preview_response_cannot_mutate_internal_confirmation_plan(service):
    before = service.repo.load()
    plan = preview(
        service, action="seeds", division_id=before.divisions[0].id, mode="none", seeds=[]
    )
    plan["summary"]["seed_rules"]["seeded_entries_mode"] = "provided"
    confirm(service, plan)
    after = service.repo.load()
    assert {**after.config["draw_defaults"], **after.divisions[0].draw_overrides}[
        "seeded_entries_mode"
    ] == "none"


def test_artifact_route_cannot_escape_completed_export_directory(service, tmp_path):
    directory = tmp_path / "export"
    directory.mkdir()
    (directory / "index.html").write_text("review", encoding="utf-8")
    (tmp_path / "private.txt").write_text("private", encoding="utf-8")
    service.jobs["test"] = {"status": "completed", "directory": directory}
    assert service.artifact("test", "index.html").read_text("utf-8") == "review"
    with pytest.raises(ValueError):
        service.artifact("test", "../private.txt")


def test_operator_codes_match_export_model_even_for_legacy_unnumbered_matches(service):
    from squash_scheduler.exporters.documents import view_model

    state = service.state()
    model = view_model(service.repo.load())
    assert {m["id"]: m["code"] for m in state["matches"]} == {
        m["match_id"]: m["code"] for m in model["matches"]
    }
