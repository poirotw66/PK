"""Organizer terms can be filled after setup without replacing event or played rules."""

from copy import deepcopy

import pytest
from test_local_workflow import setup

from squash_scheduler.local_workflow import LocalWorkflow
from squash_scheduler.operations import Conflict, OperatorService
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def workflow(tmp_path):
    service = OperatorService(Repository(tmp_path / "rules-test"))
    yield LocalWorkflow(service)
    service.close()


def preview(service, rules):
    return service.preview(
        {
            "action": "event_rules",
            "revision_id": service.repo.load().id,
            "actor": "synthetic-operator",
            "reason": "Reviewed organizer terms",
            "rules": rules,
        }
    )


def confirm(service, plan):
    return service.confirm({"id": plan["id"], "hash": plan["hash"]})


def test_fill_rules_after_setup_with_review_and_fresh_revision(workflow):
    snapshot, _ = setup(workflow)
    service = workflow.service
    plan = preview(
        service, {"late_arrival_rule": "遲到須由裁判確認", "walkover_rule": "棄權須經賽務確認"}
    )
    assert service.repo.load().id == snapshot.id
    assert plan["summary"]["changes"][0] == {
        "label": "遲到處理",
        "before": None,
        "after": "遲到須由裁判確認",
    }
    assert plan["summary"]["source_revision"] == snapshot.id
    confirm(service, plan)
    current = service.repo.load()
    assert current.id != snapshot.id
    assert workflow.state()["rules"]["late_arrival_rule"] == "遲到須由裁判確認"
    assert current.config["event"] == snapshot.config["event"]
    assert current.config["match_rule_defaults"]["retirement_rule"] is None
    assert current.participants == snapshot.participants
    assert current.changes[-1]["operation"] == "event_rules"


def test_rules_preview_rejects_stale_confirmation_and_unsupported_fields(workflow):
    snapshot, _ = setup(workflow)
    service = workflow.service
    first = preview(service, {"late_arrival_rule": "待裁判確認"})
    second = preview(service, {"retirement_rule": "須由裁判確認"})
    confirm(service, second)
    with pytest.raises(Conflict, match="已更新"):
        confirm(service, first)
    with pytest.raises(ValueError, match="只修改"):
        preview(service, {"best_of_games": 5})
    with pytest.raises(ValueError, match="沒有變更"):
        preview(service, {"retirement_rule": "須由裁判確認"})
    assert (
        service.repo.load().config["match_rule_defaults"]["best_of_games"]
        == snapshot.config["match_rule_defaults"]["best_of_games"]
    )


def test_rule_update_retains_started_match_rule_snapshot(workflow):
    initial, _ = setup(workflow)
    service = workflow.service
    for division in initial.divisions:
        plan = service.preview(
            {
                "action": "format",
                "division_id": division.id,
                "format": {
                    "template_id": "round_robin",
                    "pools": [],
                    "bronze": False,
                    "encouragement": False,
                },
                "revision_id": service.repo.load().id,
                "actor": "synthetic-operator",
                "reason": "Reviewed synthetic round robin",
            }
        )
        confirm(service, plan)
    snapshot = service.repo.load()
    started = snapshot.matches[0]
    started.status = "in_progress"
    old_rules = deepcopy(started.rule_snapshot)
    with service.repo.lock():
        service.repo.save(snapshot, snapshot.id)
    confirm(service, preview(service, {"late_arrival_rule": "遲到由裁判人工確認"}))
    current = service.repo.load()
    assert current.matches[0].rule_snapshot == old_rules
    assert current.matches[0].status == "in_progress"
    unstarted = current.matches[1]
    assert unstarted.rule_snapshot["match_rules"]["late_arrival_rule"] == "遲到由裁判人工確認"


def test_explicit_special_outcome_policy_can_be_reviewed_without_parsing_rule_prose(workflow):
    setup(workflow)
    policy = {
        "include_in_rankings": True,
        "counts_as_played": False,
        "winner_points": 2,
        "loser_points": 0,
    }
    plan = preview(
        workflow.service, {"walkover_rule": policy, "ranking_denominator_rule": {"walkover": True}}
    )
    assert plan["summary"]["changes"][0]["after"] == policy
    confirm(workflow.service, plan)
    current = workflow.service.repo.load()
    assert current.config["match_rule_defaults"]["walkover_rule"] == policy
    assert current.config["match_rule_defaults"]["ranking_denominator_rule"] == {"walkover": True}


@pytest.mark.parametrize(
    "rules",
    [
        {"walkover_rule": {"include_in_rankings": True}},
        {
            "walkover_rule": {
                "include_in_rankings": True,
                "counts_as_played": True,
                "winner_points": 2,
                "loser_points": 0,
            }
        },
        {
            "retirement_rule": {
                "include_in_rankings": True,
                "counts_as_played": True,
                "winner_points": -1,
                "loser_points": 0,
            }
        },
        {"ranking_denominator_rule": {"walkover": "yes"}},
        {"ranking_denominator_rule": {"guessed": True}},
    ],
)
def test_incomplete_special_policies_never_replace_rules(workflow, rules):
    setup(workflow)
    before = workflow.service.repo.load().id
    with pytest.raises(ValueError):
        preview(workflow.service, rules)
    assert workflow.service.repo.load().id == before
