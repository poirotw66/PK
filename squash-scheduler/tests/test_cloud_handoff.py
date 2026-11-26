"""Local file handoffs preserve scores and reject conflicting version lineages."""

import json
import shutil
import subprocess
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from test_local_workflow import setup

from squash_scheduler.cloud_handoff import execute_cloud_return, export_cloud_schedule
from squash_scheduler.domain.models import canonical_hash, utcnow
from squash_scheduler.local_workflow import LocalWorkflow
from squash_scheduler.operations import Conflict, OperatorService
from squash_scheduler.scheduling import validate_schedule
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def service(tmp_path):
    operator = OperatorService(Repository(tmp_path / "event"))
    initial, _ = setup(LocalWorkflow(operator))
    for division in initial.divisions:
        apply(
            operator,
            "format",
            division_id=division.id,
            format={
                "template_id": "round_robin",
                "pools": [],
                "bronze": False,
                "encouragement": False,
            },
        )
    apply(operator, "schedule")
    yield operator
    operator.close()


def apply(service, action, **values):
    plan = service.preview(
        {
            "action": action,
            "actor": "synthetic-operator",
            "reason": "Reviewed cloud file handoff",
            "revision_id": service.repo.load().id,
            **values,
        }
    )
    return service.confirm({"id": plan["id"], "hash": plan["hash"]})


def seal(envelope):
    envelope["sha256"] = canonical_hash({k: v for k, v in envelope.items() if k != "sha256"})
    return envelope


def returned(service):
    state = export_cloud_schedule(service.repo)
    state = state.get("state", state)
    receipt = next(
        (c for c in reversed(service.repo.load().changes) if c.get("kind") == "cloud_return"), None
    )
    return seal(
        {
            "format": "pk-cloud-return-v1",
            "source_revision": state["source_revision"],
            "cloud_revision": "synthetic-cloud-1",
            "origin": "https://admin.example.test",
            "exported_at": utcnow().isoformat(),
            "sequence": 1,
            "state": state,
            "live": deepcopy(receipt["live"]) if receipt else {"starts": {}, "actual_courts": {}},
        }
    )


def finish(envelope, index=0):
    match = envelope["state"]["matches"][index]
    match["status"] = "completed"
    match["result"] = {
        "rules": match["scoring_rules"],
        "winner": "a",
        "games": [{"side_a": 11, "side_b": 4}] * 2,
    }
    envelope["live"]["starts"][match["id"]] = (utcnow() - timedelta(minutes=30)).isoformat()
    envelope["live"]["actual_courts"][match["id"]] = envelope["state"]["courts"][1]["id"]
    return seal(envelope)


def test_return_preview_confirm_preserves_result_actual_start_and_original(service):
    before = service.repo.load()
    envelope = finish(returned(service))
    plan = service.preview(
        {
            "action": "cloud_return",
            "envelope": envelope,
            "actor": "synthetic-operator",
            "reason": "Checked score sheet",
            "revision_id": before.id,
        }
    )
    assert service.repo.load().id == before.id
    assert not service.repo.load().results
    assert plan["summary"]["result_count"] == 1
    service.confirm({"id": plan["id"], "hash": plan["hash"]})
    saved = service.repo.load()
    assert saved.id != before.id
    result = saved.results[0]
    assert result["status"] == "confirmed" and result["games"][0]["side_a"] == 11
    assert next(m for m in saved.matches if m.id == result["match_id"]).status == "completed"
    receipt = next(c for c in saved.changes if c.get("kind") == "cloud_return")
    assert receipt["live"] == envelope["live"]
    assert not service.repo.load(before.id).results
    exported = export_cloud_schedule(service.repo)
    assert exported["format"] == "pk-local-update-v1"
    assert exported["base_cloud_revision"] == envelope["cloud_revision"]
    assert exported["state"]["source_revision"] == saved.id
    assert exported["state"]["matches"] == envelope["state"]["matches"]
    assert exported["sha256"] == canonical_hash(
        {k: v for k, v in exported.items() if k != "sha256"}
    )
    with pytest.raises(ValueError, match="已取回"):
        execute_cloud_return(saved, service.repo, envelope, "synthetic", "duplicate")


@pytest.mark.parametrize(
    "mutation",
    [
        "checksum",
        "origin",
        "rules",
        "identity",
        "active",
        "score",
        "matches",
        "actual_court",
        "start_time",
        "status",
    ],
)
def test_invalid_returns_never_change_workspace(service, mutation):
    envelope = returned(service)
    row = envelope["state"]["matches"][0]
    if mutation == "checksum":
        envelope["sha256"] = "0" * 64
    elif mutation == "origin":
        envelope["origin"] = "http://admin.example.test"
    elif mutation == "rules":
        envelope["state"]["daily_limit"] = 100
    elif mutation == "identity":
        row["side_a"] = "Changed identity"
    elif mutation == "active":
        row["status"] = "in_progress"
    elif mutation == "score":
        finish(envelope)
        row["result"]["games"][0] = {"side_a": 3, "side_b": 4}
    elif mutation == "matches":
        envelope["state"]["matches"].append(deepcopy(row))
    elif mutation == "actual_court":
        finish(envelope)
        envelope["live"]["actual_courts"][row["id"]] = "unknown"
    elif mutation == "start_time":
        finish(envelope)
        envelope["live"]["starts"][row["id"]] = (utcnow() + timedelta(days=1)).isoformat()
    else:
        row["status"] = "completed"
    if mutation != "checksum":
        seal(envelope)
    original = service.repo.load().model_dump(mode="json")
    with pytest.raises((ValueError, TypeError)):
        execute_cloud_return(service.repo.load(), service.repo, envelope, "synthetic", "review")
    assert service.repo.load().model_dump(mode="json") == original


def test_stale_local_head_and_stale_confirmation_are_rejected(service):
    envelope = returned(service)
    current = service.repo.load()
    plan = service.preview(
        {
            "action": "cloud_return",
            "envelope": envelope,
            "actor": "synthetic",
            "reason": "Reviewed return",
            "revision_id": current.id,
        }
    )
    apply(service, "seeds", division_id=current.divisions[-1].id, mode="none", seeds=[])
    with pytest.raises(Conflict):
        service.confirm({"id": plan["id"], "hash": plan["hash"]})
    with pytest.raises(ValueError, match="來源不一致"):
        execute_cloud_return(service.repo.load(), service.repo, envelope, "synthetic", "stale")


def test_second_return_requires_same_site_and_no_intervening_local_edits(service):
    envelope = returned(service)
    apply(service, "cloud_return", envelope=envelope)
    next_return = deepcopy(envelope)
    next_return.update(
        cloud_revision="synthetic-cloud-2", exported_at=utcnow().isoformat(), sequence=2
    )
    finish(next_return, 1)
    apply(service, "cloud_return", envelope=next_return)
    assert len(service.repo.load().results) == 1
    current = service.repo.load()
    next_return.update(
        cloud_revision="synthetic-cloud-3", exported_at=utcnow().isoformat(), sequence=3
    )
    next_return["origin"] = "https://another.example.test"
    seal(next_return)
    with pytest.raises(ValueError, match="不同"):
        execute_cloud_return(current, service.repo, next_return, "synthetic", "wrong site")
    next_return["origin"] = envelope["origin"]
    seal(next_return)
    apply(service, "seeds", division_id=current.divisions[-1].id, mode="none", seeds=[])
    with pytest.raises(ValueError, match="取回後修改"):
        execute_cloud_return(service.repo.load(), service.repo, next_return, "synthetic", "stale")


def test_republished_source_can_return_without_losing_results(service):
    first = finish(returned(service))
    apply(service, "cloud_return", envelope=first)
    second = returned(service)
    second.update(cloud_revision="synthetic-cloud-2", sequence=2)
    finish(second, 1)
    apply(service, "cloud_return", envelope=seal(second))
    assert len(service.repo.load().results) == 2
    assert export_cloud_schedule(service.repo)["state"]["matches"] == second["state"]["matches"]


def test_schedule_changes_are_reviewed_and_conflicts_and_locks_are_blocked(service):
    envelope = returned(service)
    for row in envelope["state"]["matches"]:
        row["assignment"]["start"] += 15
    _, summary = execute_cloud_return(
        service.repo.load(), service.repo, seal(envelope), "synthetic", "review schedule"
    )
    assert len(summary["schedule_changes"]) == len(envelope["state"]["matches"])
    apply(service, "cloud_return", envelope=envelope)
    assert export_cloud_schedule(service.repo)["state"]["matches"] == envelope["state"]["matches"]
    locked = service.repo.load()
    locked.assignments[0].locks["time"] = True
    with service.repo.lock():
        service.repo.save(locked, locked.id)
    with pytest.raises(ValueError, match="只能調整"):
        export_cloud_schedule(service.repo)


def test_cloud_return_cannot_move_locked_assignment(service):
    locked = service.repo.load()
    locked.assignments[0].locks["time"] = True
    with service.repo.lock():
        service.repo.save(locked, locked.id)
    changed = returned(service)
    changed["state"]["matches"][0]["assignment"]["start"] += 15
    with pytest.raises(ValueError):
        execute_cloud_return(
            service.repo.load(), service.repo, seal(changed), "synthetic", "locked assignment"
        )


def test_conflicting_cloud_assignments_cannot_be_applied(service):
    envelope = returned(service)
    position = envelope["state"]["matches"][0]["assignment"]
    for row in envelope["state"]["matches"]:
        row["assignment"] = deepcopy(position)
    with pytest.raises(ValueError, match="衝突"):
        execute_cloud_return(
            service.repo.load(),
            service.repo,
            seal(envelope),
            "synthetic",
            "conflicting assignments",
        )


def test_local_schedule_outside_cloud_start_window_cannot_be_exported(service):
    snapshot = service.repo.load()
    for court in snapshot.config["courts"]:
        court["availability"][0]["start_at"] = "2030-01-01T06:00:00+08:00"
    snapshot.config["timing_defaults"]["daily_grid_anchors"][0]["local_time"] = "06:00"
    for assignment in snapshot.assignments:
        assignment.start_at -= timedelta(hours=2)
        assignment.play_end_at -= timedelta(hours=2)
        assignment.court_release_at -= timedelta(hours=2)
    assert not [issue for issue in validate_schedule(snapshot) if issue.severity == "error"]
    with service.repo.lock():
        service.repo.save(snapshot, snapshot.id)
    with pytest.raises(ValueError, match="08:00 至 17:30"):
        export_cloud_schedule(service.repo)


def test_republished_return_cannot_remove_known_actual_start(service):
    apply(service, "cloud_return", envelope=finish(returned(service)))
    envelope = returned(service)
    envelope.update(cloud_revision="synthetic-cloud-2", sequence=2)
    envelope["live"] = {"starts": {}, "actual_courts": {}}
    with pytest.raises(ValueError, match="開賽紀錄"):
        execute_cloud_return(
            service.repo.load(), service.repo, seal(envelope), "synthetic", "missing actual start"
        )


def test_real_javascript_export_and_local_update_contract(service):
    root = Path(__file__).resolve().parents[2]
    outputs = root / "sites" / "squash-admin" / "outputs"
    node = shutil.which("node")
    if not node or not (outputs / "local-return-model.mjs").is_file():
        pytest.skip("Build the admin pure handoff bundles and install Node for cross-language QA")
    script = """
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
globalThis.crypto ??= webcrypto;
const root=process.argv[1], input=JSON.parse(readFileSync(0,'utf8'));
const {buildCloudReturn}=await import(pathToFileURL(root+'/local-return-model.mjs'));
const {reviewLocalUpdate}=await import(pathToFileURL(root+'/local-update-model.mjs'));
if(input.update) {
  const reviewed=await reviewLocalUpdate(input.current,input.update,'https://admin.example.test');
  console.log(JSON.stringify({state:reviewed.state,changes:reviewed.changes}));
} else {
  const state=input.state, match=state.matches[0];
  match.status='completed';
  match.result={rules:match.scoring_rules,winner:'a',games:[{side_a:11,side_b:4},{side_a:11,side_b:4}]};
  console.log(JSON.stringify(await buildCloudReturn({state,cloud_revision:'synthetic-js-cloud',
    origin:'https://admin.example.test',exported_at:new Date().toISOString(),sequence:2,
    live:null,start_history:[]})));
}
"""

    def run(payload):
        result = subprocess.run(
            [node, "--input-type=module", "-e", script, str(outputs)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return json.loads(result.stdout)

    envelope = run({"state": export_cloud_schedule(service.repo)})
    apply(service, "cloud_return", envelope=envelope)
    update = export_cloud_schedule(service.repo)
    checked = run({"current": envelope["state"], "update": update})
    assert checked["state"] == update["state"]
    assert checked["changes"] == []
    assert checked["state"]["matches"][0]["result"] == envelope["state"]["matches"][0]["result"]


def test_special_outcomes_require_explicit_local_rules_and_can_travel_in_reviewed_updates(service):
    initial = export_cloud_schedule(service.repo)
    assert all("walkover" not in match["allowed_outcomes"] for match in initial["matches"])
    envelope = returned(service)
    apply(service, "cloud_return", envelope=envelope)
    rules = {
        "walkover_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "winner_points": 3,
            "loser_points": 0,
        },
        "ranking_denominator_rule": {
            "played": True,
            "walkover": True,
            "retired": True,
            "double_forfeit": True,
        },
    }
    apply(service, "event_rules", rules=rules)
    updated = export_cloud_schedule(service.repo)
    assert updated["format"] == "pk-local-update-v1"
    assert all("walkover" in match["allowed_outcomes"] for match in updated["state"]["matches"])
    assert updated["state"]["source_revision"] == service.repo.load().id
