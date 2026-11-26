"""Explicit one/two-player dispositions keep local intake schedulable."""

from copy import deepcopy

import pytest
from test_local_workflow import request

from squash_scheduler.cloud_handoff import export_cloud_schedule
from squash_scheduler.domain.validation import approval_valid, validate
from squash_scheduler.local_workflow import LocalWorkflow
from squash_scheduler.operations import OperatorService
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def workflow(tmp_path):
    service = OperatorService(Repository(tmp_path / "mixed-small-groups"))
    yield LocalWorkflow(service)
    service.close()


def mixed_request(workflow):
    payload = request(workflow)
    payload["rows"][-1]["division"] = "Synthetic Single"
    classifications = payload["settings"]["division_classifications"]
    classifications["Synthetic Men B"]["small_count_action"] = "direct_match"
    classifications["Synthetic Single"] = {
        "category": "competitive",
        "gender_category": "mixed",
        "small_count_action": "exhibition_only",
    }
    return payload


@pytest.mark.parametrize("group", ["Synthetic Men B", "Synthetic Single"])
def test_small_groups_require_explicit_review_without_creating_event(workflow, group):
    payload = mixed_request(workflow)
    payload["settings"]["division_classifications"][group].pop("small_count_action")
    with pytest.raises(ValueError, match="人組處置"):
        workflow.preview(payload)
    assert not workflow.initialized()


def test_small_group_treatment_must_match_actual_reviewed_roster(workflow):
    payload = mixed_request(workflow)
    payload["settings"]["division_classifications"]["Synthetic Men B"]["small_count_action"] = (
        "exhibition_only"
    )
    with pytest.raises(ValueError, match="2 人組處置"):
        workflow.preview(payload)
    assert not workflow.initialized()


def test_mixed_roster_reaches_real_schedule_and_cloud_export(workflow):
    payload = mixed_request(workflow)
    original = deepcopy(payload)
    plan = workflow.preview(payload)
    assert payload == original
    assert [(row["entries"], row["action"]) for row in plan["summary"]["small_groups"]] == [
        (2, "direct_match"),
        (1, "exhibition_only"),
    ]
    assert not workflow.initialized()
    workflow.confirm({"id": plan["id"], "hash": plan["hash"]})
    initial = workflow.service.repo.load()
    assert len(initial.entries) == len(initial.participants) == 6
    single = next(d for d in initial.divisions if d.name == "Synthetic Single")
    pair = next(d for d in initial.divisions if d.name == "Synthetic Men B")
    assert single.status == "exhibition_only"
    assert all(d.minimum_entries == 3 for d in initial.divisions)
    resolution = pair.rule_overrides["count_resolution"]
    assert resolution["action"] == "direct_match"
    assert resolution["entry_ids"] == sorted(
        e.id for e in initial.entries if e.effective_division_id == pair.id
    )
    assert approval_valid(initial, "division_count", pair.id, resolution)
    service = workflow.service

    def apply(action, **values):
        proposed = service.preview(
            {
                "action": action,
                "actor": "synthetic-operator",
                "reason": "Reviewed mixed roster",
                "revision_id": service.repo.load().id,
                **values,
            }
        )
        return service.confirm({"id": proposed["id"], "hash": proposed["hash"]})

    for division in initial.divisions:
        if division.status == "active":
            apply(
                "format",
                division_id=division.id,
                format={
                    "template_id": "round_robin",
                    "pools": [],
                    "bronze": False,
                    "encouragement": False,
                },
            )
    configured = service.repo.load()
    for gate in ("G2", "G3"):
        report = validate(configured, gate)
        assert report["passed"], report["issues"]
    assert not next(d for d in service.state()["divisions"] if d["id"] == single.id)["format"][
        "editable"
    ]
    apply("schedule")
    scheduled = service.repo.load()
    assert len(scheduled.matches) == len(scheduled.assignments) == 4
    assert not any(m.division_id == single.id for m in scheduled.matches)
    assert len([m for m in scheduled.matches if m.division_id == pair.id]) == 1
    report = validate(scheduled, "G4")
    assert report["passed"], report["issues"]
    cloud = export_cloud_schedule(service.repo)
    assert len(cloud["matches"]) == 4
    assert len(cloud["players"]) == 6
