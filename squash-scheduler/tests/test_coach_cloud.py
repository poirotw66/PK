"""Coach rules travel through reviewed cloud handoffs without changing old scores."""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_cloud_handoff import apply, returned, seal

from squash_scheduler.cloud_handoff import execute_cloud_return, export_cloud_schedule
from squash_scheduler.domain.models import Assignment, Match, Stage
from squash_scheduler.domain.rules import refresh_rule_snapshots

pytest_plugins = ["test_cloud_handoff"]


def add_final(service):
    current = service.repo.load()
    first = current.matches[0]
    stage = Stage(division_id=first.division_id, type="elimination")
    final = Match(
        division_id=first.division_id,
        stage_id=stage.id,
        side_a=first.side_a,
        side_b=first.side_b,
        round=2,
        display_code="COACH-FINAL",
    )
    current.stages.append(stage)
    current.matches.append(final)
    start = max(a.court_release_at for a in current.assignments) + timedelta(minutes=90)
    current.assignments.append(
        Assignment(
            match_id=final.id,
            court_id=current.assignments[0].court_id,
            start_at=start,
            play_end_at=start + timedelta(minutes=20),
            court_release_at=start + timedelta(minutes=30),
        )
    )
    refresh_rule_snapshots(current)
    with service.repo.lock():
        service.repo.save(current, current.id)
    return final.id


def enable_cloud_profile(envelope, final_id):
    envelope["state"]["coach_profile"] = {"version": 1, "championship_ids": [final_id]}
    for match in envelope["state"]["matches"]:
        match["allowed_outcomes"] = list(
            dict.fromkeys(match.get("allowed_outcomes", ["played"]) + ["walkover"])
        )
    row = next(m for m in envelope["state"]["matches"] if m["id"] == final_id)
    row["scoring_rules"]["best_of"] = 5
    return row


def test_export_final_five_and_other_three_after_review(service):
    final_id = add_final(service)
    apply(service, "coach_rules")
    exported = export_cloud_schedule(service.repo)
    assert exported["coach_profile"] == {"version": 1, "championship_ids": [final_id]}
    for match in exported["matches"]:
        assert match["championship"] == (match["id"] == final_id)
        assert match["scoring_rules"]["best_of"] == (5 if match["championship"] else 3)


def test_reviewed_cloud_profile_returns_three_win_final_and_preserves_source(service):
    final_id = add_final(service)
    source = service.repo.load()
    envelope = returned(service)
    row = enable_cloud_profile(envelope, final_id)
    row["status"] = "completed"
    row["result"] = {
        "rules": deepcopy(row["scoring_rules"]),
        "winner": "a",
        "games": [{"side_a": 11, "side_b": 5}] * 3,
    }
    working, summary = execute_cloud_return(
        source, service.repo, seal(envelope), "coach", "reviewed policy"
    )
    assert summary["result_count"] == 1
    assert working.config["runtime"]["coach_competition_rules"] is True
    assert working.results[-1]["rule_snapshot"]["best_of_games"] == 5
    assert not source.results
    assert not source.config.get("runtime", {}).get("coach_competition_rules")


def test_false_final_profile_cannot_change_non_final_rules(service):
    final_id = add_final(service)
    envelope = returned(service)
    other = next(m for m in envelope["state"]["matches"] if m["id"] != final_id)
    enable_cloud_profile(envelope, final_id)
    envelope["state"]["coach_profile"]["championship_ids"] = [other["id"]]
    with pytest.raises(ValueError, match="冠軍場次"):
        execute_cloud_return(service.repo.load(), service.repo, seal(envelope), "coach", "invalid")


def test_local_review_after_cloud_return_exports_explicit_rule_update(service):
    final_id = add_final(service)
    apply(service, "cloud_return", envelope=returned(service))
    apply(service, "coach_rules")
    update = export_cloud_schedule(service.repo)
    assert update["format"] == "pk-local-update-v1"
    assert update["state"]["coach_profile"]["championship_ids"] == [final_id]
    final = next(m for m in update["state"]["matches"] if m["id"] == final_id)
    assert final["scoring_rules"]["best_of"] == 5


def test_legacy_rows_without_final_marker_still_return(service):
    envelope = returned(service)
    for match in envelope["state"]["matches"]:
        match.pop("championship", None)
    working, summary = execute_cloud_return(
        service.repo.load(), service.repo, seal(envelope), "coach", "legacy"
    )
    assert summary["result_count"] == 0
    assert not working.config.get("runtime", {}).get("coach_competition_rules")


def test_coach_profile_enables_unplayed_legacy_walkover_without_synthetic_games(service):
    final_id = add_final(service)
    current = service.repo.load()
    current.config["match_rule_defaults"]["walkover_rule"] = None
    current.config["match_rule_defaults"]["ranking_denominator_rule"] = None
    refresh_rule_snapshots(current)
    with service.repo.lock():
        service.repo.save(current, current.id)
    envelope = returned(service)
    row = enable_cloud_profile(envelope, final_id)
    for match in envelope["state"]["matches"]:
        match["allowed_outcomes"] = list(dict.fromkeys(match["allowed_outcomes"] + ["walkover"]))
    row["status"] = "completed"
    row["result"] = {
        "rules": deepcopy(row["scoring_rules"]),
        "winner": "a",
        "games": [],
        "outcome": "walkover",
    }
    working, _ = execute_cloud_return(
        service.repo.load(), service.repo, seal(envelope), "coach", "walkover"
    )
    result = working.results[-1]
    assert result["outcome"] == "walkover" and result["games"] == []
    assert result["rule_snapshot"]["walkover_rule"]["counts_as_played"] is False


def test_real_js_reviewed_coach_profile_returns_to_python(service):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    final_id = add_final(service)
    outputs = Path(__file__).resolve().parents[2] / "sites/squash-admin/outputs"
    node = shutil.which("node")
    if not node or not (outputs / "coach-rules-model.mjs").is_file():
        pytest.skip("Build the reviewed coach policy bundle for cross-language QA")
    script = """
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
globalThis.crypto ??= webcrypto;
const input=JSON.parse(readFileSync(0,'utf8'));
const {reviewCoachRules}=await import(pathToFileURL(process.argv[1]+'/coach-rules-model.mjs'));
const {buildCloudReturn}=await import(pathToFileURL(process.argv[1]+'/local-return-model.mjs'));
const {state}=reviewCoachRules(input.state,[input.final_id]);
const final=state.matches.find(match=>match.id===input.final_id);
final.status='completed';final.result={rules:final.scoring_rules,winner:'a',games:Array(3).fill({side_a:11,side_b:5})};
console.log(JSON.stringify(await buildCloudReturn({state,cloud_revision:'js-coach',origin:'https://admin.example.test',exported_at:new Date().toISOString(),sequence:2,live:null,start_history:[]})));
"""
    outcome = subprocess.run(
        [node, "--input-type=module", "-e", script, str(outputs)],
        input=json.dumps({"state": export_cloud_schedule(service.repo), "final_id": final_id}),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    envelope = json.loads(outcome.stdout)
    working, summary = execute_cloud_return(
        service.repo.load(), service.repo, envelope, "coach", "actual JS profile"
    )
    assert summary["result_count"] == 1
    assert working.results[-1]["rule_snapshot"]["best_of_games"] == 5


def test_real_js_accepts_local_coach_policy_update(service):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    final_id = add_final(service)
    envelope = returned(service)
    apply(service, "cloud_return", envelope=envelope)
    apply(service, "coach_rules")
    update = export_cloud_schedule(service.repo)
    outputs = Path(__file__).resolve().parents[2] / "sites/squash-admin/outputs"
    node = shutil.which("node")
    if not node or not (outputs / "local-update-model.mjs").is_file():
        pytest.skip("Build the local update bundle for cross-language QA")
    script = """
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
globalThis.crypto ??= webcrypto;
const input=JSON.parse(readFileSync(0,'utf8'));
const {reviewLocalUpdate}=await import(pathToFileURL(process.argv[1]+'/local-update-model.mjs'));
console.log(JSON.stringify(await reviewLocalUpdate(input.current,input.update,'https://admin.example.test')));
"""
    outcome = subprocess.run(
        [node, "--input-type=module", "-e", script, str(outputs)],
        input=json.dumps({"current": envelope["state"], "update": update}),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    checked = json.loads(outcome.stdout)
    assert checked["state"] == update["state"]
    assert (
        next(m for m in checked["state"]["matches"] if m["id"] == final_id)["scoring_rules"][
            "best_of"
        ]
        == 5
    )
