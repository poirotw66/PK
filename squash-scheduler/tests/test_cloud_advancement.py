"""Explicit cloud advancement metadata survives the real local return contract."""

import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from test_cloud_handoff import apply, returned, seal

from squash_scheduler.cloud_handoff import (
    execute_cloud_return,
    export_advancement_sources,
    export_cloud_schedule,
)
from squash_scheduler.domain.models import Assignment, LoserSide, Match, Stage, WinnerSide
from squash_scheduler.domain.rankings import resolve_side

pytest_plugins = ["test_cloud_handoff"]


def add_placement(service):
    current = service.repo.load()
    original = current.matches[0]
    stage = Stage(division_id=original.division_id, type="placement", label="Reviewed placement")
    future = Match(
        division_id=original.division_id,
        stage_id=stage.id,
        kind="placement",
        side_a=WinnerSide(match_id=original.id),
        side_b=LoserSide(match_id=original.id),
        display_code="ADV",
    )
    start = max(row.court_release_at for row in current.assignments) + timedelta(minutes=90)
    current.stages.append(stage)
    current.matches.append(future)
    current.assignments.append(
        Assignment(
            match_id=future.id,
            court_id=current.assignments[0].court_id,
            start_at=start,
            play_end_at=start + timedelta(minutes=20),
            court_release_at=start + timedelta(minutes=30),
        )
    )
    with service.repo.lock():
        saved = service.repo.save(current, current.id)
    return saved, original.id, future.id


def test_export_has_explicit_side_sources_and_does_not_change_local_revision(service):
    before = service.repo.load().id
    state = export_advancement_sources(service.repo)
    assert state["source_revision"] == before
    assert len(state["entries"]) == len(service.repo.load().entries)
    assert all(match["slots"]["a"]["kind"] == "entry" for match in state["matches"])
    assert service.repo.load().id == before


def test_real_js_advancement_roundtrip_and_identity_tampering(service):
    local, first_id, next_id = add_placement(service)
    state = export_cloud_schedule(service.repo)
    first = next(row for row in state["matches"] if row["id"] == first_id)
    first["status"] = "completed"
    first["result"] = {
        "rules": first["scoring_rules"],
        "winner": "b",
        "games": [{"side_a": 4, "side_b": 11}, {"side_a": 7, "side_b": 11}],
    }
    outputs = Path(__file__).resolve().parents[2] / "sites/squash-admin/outputs"
    node = shutil.which("node")
    if not node or not (outputs / "advancement-model.mjs").is_file():
        pytest.skip("Build the real admin advancement bundle for contract QA")
    script = """
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
globalThis.crypto ??= webcrypto;
const {reviewAdvancement}=await import(pathToFileURL(process.argv[1]+'/advancement-model.mjs'));
const {buildCloudReturn}=await import(pathToFileURL(process.argv[1]+'/local-return-model.mjs'));
const result=reviewAdvancement(JSON.parse(readFileSync(0,'utf8')));
if(!result.can_apply)throw new Error(JSON.stringify(result));
console.log(JSON.stringify(await buildCloudReturn({state:result.state,cloud_revision:'js-advancement',origin:'https://admin.example.test',exported_at:new Date().toISOString(),sequence:2,live:null,start_history:[]})));
"""
    outcome = subprocess.run(
        [node, "--input-type=module", "-e", script, str(outputs)],
        input=json.dumps(state),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    envelope = json.loads(outcome.stdout)
    cloud = next(row for row in envelope["state"]["matches"] if row["id"] == next_id)
    assert cloud["entry_ids"]["a"] == first["entry_ids"]["b"]
    working, _ = execute_cloud_return(
        local, service.repo, envelope, "operator", "Reviewed advancement"
    )
    future = next(match for match in working.matches if match.id == next_id)
    assert resolve_side(working, future.side_a) == cloud["entry_ids"]["a"]
    assert len(working.results) == 1
    cloud["side_a"] = "Unapproved participant"
    seal(envelope)
    with pytest.raises(ValueError, match="姓名"):
        execute_cloud_return(local, service.repo, envelope, "operator", "Reject altered identity")


def test_original_source_export_survives_return_and_new_local_revision(service):
    original = export_cloud_schedule(service.repo)
    envelope = returned(service)
    first = envelope["state"]["matches"][0]
    first["status"] = "completed"
    first["result"] = {
        "rules": first["scoring_rules"],
        "winner": "a",
        "games": [{"side_a": 11, "side_b": 2}, {"side_a": 11, "side_b": 4}],
    }
    seal(envelope)
    apply(service, "cloud_return", envelope=envelope)
    assert service.repo.load().id != original["source_revision"]
    sources = export_advancement_sources(service.repo)
    assert sources == original


def test_pool_ordering_replays_as_content_bound_local_approval(service):
    from squash_scheduler.cloud_handoff import _cloud_pool_basis
    from squash_scheduler.domain.models import Pool, PoolRankSide

    current = service.repo.load()
    stage = current.stages[0]
    members = [
        entry.id for entry in current.entries if entry.effective_division_id == stage.division_id
    ]
    pool = Pool(stage_id=stage.id, label="Reviewed pool", entry_ids=members, advancement_count=2)
    placement = Stage(division_id=stage.division_id, type="placement", label="Pool final")
    final = Match(
        division_id=stage.division_id,
        stage_id=placement.id,
        kind="placement",
        side_a=PoolRankSide(pool_id=pool.id, rank=1),
        side_b=PoolRankSide(pool_id=pool.id, rank=2),
        display_code="POOL",
    )
    current.pools.append(pool)
    current.stages.append(placement)
    current.matches.append(final)
    start = max(row.court_release_at for row in current.assignments) + timedelta(minutes=90)
    current.assignments.append(
        Assignment(
            match_id=final.id,
            court_id=current.assignments[0].court_id,
            start_at=start,
            play_end_at=start + timedelta(minutes=20),
            court_release_at=start + timedelta(minutes=30),
        )
    )
    with service.repo.lock():
        saved = service.repo.save(current, current.id)
    envelope = returned(service)
    state = envelope["state"]
    descriptor = next(value for value in state["advancement_pools"] if value["id"] == pool.id)
    for row in state["matches"]:
        if row["id"] not in descriptor["match_ids"]:
            continue
        row["status"] = "completed"
        row["result"] = {
            "rules": row["scoring_rules"],
            "winner": "a",
            "games": [{"side_a": 11, "side_b": 3}, {"side_a": 11, "side_b": 4}],
        }
    state["advancement_decisions"] = [
        {
            "stage_id": stage.id,
            "pool_id": pool.id,
            "entry_ids": members,
            "basis": _cloud_pool_basis(state, descriptor),
        }
    ]
    entries = {entry["id"]: entry["player_id"] for entry in state["entries"]}
    names = {player["id"]: player["name"] for player in state["players"]}
    cloud_final = next(row for row in state["matches"] if row["id"] == final.id)
    cloud_final["entry_ids"] = {"a": members[0], "b": members[1]}
    cloud_final["side_a"], cloud_final["side_b"] = (
        names[entries[members[0]]],
        names[entries[members[1]]],
    )
    cloud_final["players"] = sorted([entries[members[0]], entries[members[1]]])
    seal(envelope)
    working, _ = execute_cloud_return(
        saved, service.repo, envelope, "operator", "Reviewed all group scores and tie disposition"
    )
    resolved = next(match for match in working.matches if match.id == final.id)
    assert resolve_side(working, resolved.side_a) == members[0]
    assert resolve_side(working, resolved.side_b) == members[1]
    assert working.ranking_decisions[-1]["status"] == "approved"
    assert working.ranking_decisions[-1]["approval_id"]
