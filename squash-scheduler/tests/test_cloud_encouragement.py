"""Real admin draw output returns to canonical local geometry without score loss."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from test_cloud_handoff import seal
from test_encouragement_draws import qualified

from squash_scheduler.cloud_handoff import execute_cloud_return, export_cloud_schedule
from squash_scheduler.domain.encouragement_draws import validate_encouragement
from squash_scheduler.domain.models import utcnow
from squash_scheduler.domain.rules import refresh_rule_snapshots
from squash_scheduler.scheduling import solve_schedule, validate_schedule
from squash_scheduler.storage.repository import Repository


def cloud_draw(tmp_path, pending=False):
    snapshot, division_id = qualified(size=8)
    snapshot.config["runtime"]["coach_competition_rules"] = True
    snapshot.config["player_workload"] = {
        "minimum_rest_minutes": 60,
        "max_matches_per_day": 5,
        "participant_scope": "confirmed_only",
    }
    snapshot.config["timing_defaults"]["minimum_player_rest_minutes"] = 60
    refresh_rule_snapshots(snapshot)
    assert solve_schedule(snapshot, time_limit_seconds=10)["status"] in {"FEASIBLE", "OPTIMAL"}
    completed = deepcopy(snapshot.results)
    if pending:
        snapshot.results = []
        for match in snapshot.matches:
            match.status = "planned"
    repo = Repository(tmp_path / "event")
    snapshot = repo.save(snapshot, None)
    state = export_cloud_schedule(repo)
    if pending:
        for row in state["matches"]:
            evidence = next(
                (result for result in completed if result["match_id"] == row["id"]), None
            )
            if evidence:
                row["status"] = "completed"
                row["result"] = {
                    "rules": row["scoring_rules"],
                    "winner": "a" if evidence["winner_id"] == row["entry_ids"]["a"] else "b",
                    "games": evidence["games"],
                }
    model = Path(__file__).parents[2] / "sites/squash-admin/outputs/encouragement-model.mjs"
    script = f"import {{reviewEncouragement}} from {json.dumps(model.as_uri())};let raw='';for await(const chunk of process.stdin)raw+=chunk;const state=JSON.parse(raw);const plan=reviewEncouragement(state,{json.dumps(division_id)},12345,{json.dumps(utcnow().isoformat())});if(!plan.can_apply)throw Error(JSON.stringify(plan.issues));process.stdout.write(JSON.stringify(plan.state));"
    generated = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(state),
        text=True,
        capture_output=True,
        check=True,
    )
    state = json.loads(generated.stdout)
    envelope = seal(
        {
            "format": "pk-cloud-return-v1",
            "source_revision": snapshot.id,
            "cloud_revision": "synthetic-encouragement",
            "origin": "https://admin.example.test",
            "exported_at": utcnow().isoformat(),
            "sequence": 1,
            "state": state,
            "live": {"starts": {}, "actual_courts": {}},
        }
    )
    return repo, snapshot, envelope


def test_admin_draw_returns_to_canonical_and_replans(tmp_path):
    repo, original, envelope = cloud_draw(tmp_path)
    old_results = deepcopy(original.results)
    working, report = execute_cloud_return(
        original, repo, envelope, "operator", "Review cloud draw"
    )
    assert working.results == old_results
    assert report["result_count"] == 0
    assert validate_encouragement(working) == []
    assert not [issue for issue in validate_schedule(working) if issue.severity == "error"]
    additions = [
        match for match in working.matches if match.id not in {m.id for m in original.matches}
    ]
    assert len(additions) == 3
    assert any(
        side.type == "match_loser" for match in additions for side in (match.side_a, match.side_b)
    )
    repo.save(working, original.id)
    exported = export_cloud_schedule(repo)
    assert exported["state"]["encouragement_draws"] == envelope["state"]["encouragement_draws"]
    assert exported["state"]["matches"] == envelope["state"]["matches"]


@pytest.mark.parametrize("tamper", ["seed", "bracket", "source", "extra", "old_result"])
def test_tampered_cloud_draw_fails_without_mutating_original(tmp_path, tamper):
    repo, original, envelope = cloud_draw(tmp_path)
    before = original.model_dump(mode="json")
    state = envelope["state"]
    draw = state["encouragement_draws"][0]
    if tamper == "seed":
        draw["seed"] += 1
    if tamper == "bracket":
        draw["bracket_entry_ids"].reverse()
    if tamper == "source":
        draw["source_match_ids"].pop()
    if tamper == "extra":
        state["matches"].append(deepcopy(state["matches"][-1]))
    if tamper == "old_result":
        state["matches"][0]["result"]["winner"] = "b"
    seal(envelope)
    with pytest.raises((ValueError, TypeError, KeyError)):
        execute_cloud_return(original, repo, envelope, "operator", "Reject tampering")
    assert original.model_dump(mode="json") == before


def test_cloud_first_round_results_and_draw_return_together(tmp_path):
    repo, original, envelope = cloud_draw(tmp_path, pending=True)
    working, report = execute_cloud_return(
        original, repo, envelope, "operator", "Review newly finished first round"
    )
    assert len(working.results) == 4
    assert report["result_count"] == 4
    assert validate_encouragement(working) == []
    repo.save(working, original.id)
    assert (
        export_cloud_schedule(repo)["state"]["encouragement_draws"]
        == envelope["state"]["encouragement_draws"]
    )


def test_legacy_original_rows_keep_their_contract_with_new_qualified_entries(tmp_path):
    repo, original, envelope = cloud_draw(tmp_path)
    state = envelope["state"]
    original_ids = {match.id for match in original.matches}
    for row in state["matches"]:
        if row["id"] in original_ids:
            row.pop("slots", None)
            row.pop("entry_ids", None)
    for key in ("encouragement_sources", "advancement_pools", "advancement_decisions"):
        state.pop(key, None)
    eligible = set(state["encouragement_draws"][0]["entry_ids"])
    state["entries"] = [row for row in state["entries"] if row["id"] in eligible]
    seal(envelope)
    working, _report = execute_cloud_return(
        original, repo, envelope, "operator", "Review legacy source IDs"
    )
    assert working.results == original.results
    assert validate_encouragement(working) == []
    repo.save(working, original.id)
    update = export_cloud_schedule(repo)["state"]
    assert update["entries"] == state["entries"]
    assert all("slots" not in row for row in update["matches"] if row["id"] in original_ids)


def test_completed_encouragement_result_imports_and_remains_fixed(tmp_path):
    repo, original, envelope = cloud_draw(tmp_path)
    draw = envelope["state"]["encouragement_draws"][0]
    row = next(row for row in envelope["state"]["matches"] if row["id"] == draw["match_ids"][0])
    row["status"] = "completed"
    row["result"] = {
        "rules": row["scoring_rules"],
        "winner": "a",
        "games": [{"side_a": 11, "side_b": 3}] * 2,
    }
    seal(envelope)
    working, report = execute_cloud_return(
        original, repo, envelope, "operator", "Review encouragement score"
    )
    assert report["result_count"] == 1
    assert working.results[:-1] == original.results
    assert validate_encouragement(working) == []
    repo.save(working, original.id)
    update = export_cloud_schedule(repo)["state"]
    assert next(match for match in update["matches"] if match["id"] == row["id"]) == row
