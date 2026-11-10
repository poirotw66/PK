"""Reviewed format changes preserve version and draw boundaries."""

import pytest
from test_formats_draws import event
from test_operator import confirm, preview

from squash_scheduler.domain.encouragement import eligibility_report
from squash_scheduler.domain.formats import pending_format_divisions
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Stage, stable_id
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis
from squash_scheduler.operations import Conflict, OperatorService
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def formats(tmp_path):
    snapshot, men = event(19)
    junior, girls = event(13)
    girls.name, girls.category = "國中女子組", "school"
    for field in ("participants", "registrations", "entries", "divisions"):
        getattr(snapshot, field).extend(getattr(junior, field))
    generate_matches(snapshot)
    repo = Repository(tmp_path / "event")
    with repo.lock():
        repo.save(snapshot, None)
    service = OperatorService(repo)
    yield service, men.id, girls.id
    service.close()


def choice(template, pools=(), *, bronze=True, encouragement=False):
    return {
        "template_id": template,
        "pools": [{"size": size, "advances": advances} for size, advances in pools],
        "bronze": bronze,
        "encouragement": encouragement,
    }


def test_format_previews_and_two_draw_confirmations(formats):
    service, men, girls = formats
    initial = service.repo.load()
    with pytest.raises(ValueError, match="須配滿本組 19 人"):
        preview(
            service, action="format", division_id=men, format=choice("pools_knockout", [(4, 2)])
        )
    mens = choice("pools_knockout", [(4, 2), *([(3, 1)] * 5)])
    plan = preview(service, action="format", division_id=men, format=mens)
    assert plan["summary"]["format"]["main_matches"] == 28
    assert service.repo.load().id == initial.id
    with pytest.raises(Conflict):
        confirm(service, {**plan, "hash": "stale"})
    confirm(service, plan)
    first = service.repo.load()
    assert pending_format_divisions(first) == {men}
    assert [m for m in first.matches if m.division_id == girls] == [
        m for m in initial.matches if m.division_id == girls
    ]
    plan = preview(
        service,
        action="format",
        division_id=girls,
        format=choice("single_elimination", encouragement=True),
    )
    assert plan["summary"]["format"]["main_matches"] == 13
    assert plan["summary"]["format"]["encouragement_max"] == 4
    confirm(service, plan)
    pending = service.repo.load()
    assert pending_format_divisions(pending) == {men, girls}
    with pytest.raises(ValueError, match="尚未依新賽制完成對戰"):
        service.export(pending.id)
    with pytest.raises(ValueError, match="尚未依新賽制完成對戰"):
        preview(service, action="schedule")
    for division_id in (men, girls):
        confirm(service, preview(service, action="draw", division_id=division_id, seed=5))
        state = service.repo.load()
        draw = next(d for d in reversed(state.draws) if d["division_id"] == division_id)
        if division_id == girls:
            assert sum(side is None for side in draw["output"]["bracket_entry_ids"]) == 3
        confirm(service, preview(service, action="approve_draw", draw_id=draw["id"]))
        if division_id == men:
            assert pending_format_divisions(service.repo.load()) == {men, girls}
    saved = service.repo.load()
    assert not pending_format_divisions(saved)
    assert sum(m.division_id == men for m in saved.matches) == 28
    assert sum(m.division_id == girls for m in saved.matches) == 13


def test_format_edits_respect_results_and_encouragement_opt_out(formats):
    service, men, girls = formats
    with pytest.raises(ValueError, match="鼓勵賽僅限"):
        preview(
            service,
            action="format",
            division_id=men,
            format=choice("single_elimination", encouragement=True),
        )
    plan = preview(service, action="format", division_id=girls, format=choice("single_elimination"))
    assert plan["summary"]["format"]["encouragement_max"] == 0
    confirm(service, plan)
    saved = service.repo.load()
    assert {entry["reason"] for entry in eligibility_report(saved, girls)["entries"]} == {
        "ENCOURAGEMENT_DISABLED"
    }
    match = next(m for m in saved.matches if m.division_id == men)
    match.status = "in_progress"
    with service.repo.lock():
        service.repo.save(saved, saved.id)
    with pytest.raises(ValueError, match="無法修改賽制"):
        preview(service, action="format", division_id=men, format=choice("single_elimination"))


def test_same_reviewed_legacy_format_keeps_its_graph(formats):
    service, men, _ = formats
    snapshot = service.repo.load()
    division = next(d for d in snapshot.divisions if d.id == men)
    approved, _ = approve(
        snapshot, f"format:{men}", target_basis(division.format_plan), "Operator", "Confirm rules"
    )
    with service.repo.lock():
        saved = service.repo.save(approved, snapshot.id)
    plan = preview(
        service,
        action="format",
        division_id=men,
        format=choice("round_robin", bronze=False),
    )
    assert plan["summary"]["can_apply"] is False
    with pytest.raises(Conflict):
        confirm(service, plan)
    assert service.repo.load().id == saved.id
    assert len(service.repo.load().matches) == len(snapshot.matches)


def test_pending_formats_do_not_allow_late_redraw_of_an_active_division(formats):
    service, men, girls = formats
    confirm(
        service,
        preview(service, action="format", division_id=men, format=choice("single_elimination")),
    )
    confirm(service, preview(service, action="draw", division_id=girls, seed=5))
    current = service.repo.load()
    draft = current.draws[-1]
    match = next(m for m in current.matches if m.division_id == girls)
    match.status = "in_progress"
    with service.repo.lock():
        saved = service.repo.save(current, current.id)
    with pytest.raises(ValueError, match="不能套用新籤表"):
        preview(service, action="approve_draw", draw_id=draft["id"])
    assert service.repo.load().id == saved.id
    assert service.repo.load().draws[-1]["status"] == draft["status"]


def test_format_change_preserves_manually_authored_placement(formats):
    service, men, _ = formats
    snapshot = service.repo.load()
    stage_id = stable_id("manual-placement-stage", men)
    snapshot.stages.append(Stage(id=stage_id, division_id=men, type="placement"))
    match = next(m for m in snapshot.matches if m.division_id == men)
    placement = match.model_copy(
        update={"id": stable_id("manual-placement", men), "stage_id": stage_id, "kind": "placement"}
    )
    snapshot.matches.append(placement)
    with service.repo.lock():
        saved = service.repo.save(snapshot, snapshot.id)
    with pytest.raises(ValueError, match="含有自訂對戰"):
        preview(service, action="format", division_id=men, format=choice("single_elimination"))
    assert service.repo.load().id == saved.id
    assert placement in service.repo.load().matches
