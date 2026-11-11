"""The capacity command rehearses future matches without writing to the workspace."""

import json
import os
import subprocess
import sys
from pathlib import Path

from test_encouragement_draws import qualified

from squash_scheduler.storage.repository import Repository

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_schedule_rehearsal_keeps_the_workspace_unchanged(tmp_path):
    # Each first-round pair has one seed, so a simulated non-seed loser is available in all four.
    snapshot, _ = qualified(0)
    snapshot.results.clear()
    for match in snapshot.matches:
        match.status = "planned"
    repo = Repository(tmp_path / "tournament")
    with repo.lock():
        stored = repo.save(snapshot, None)

    def workspace_files():
        return {
            path.relative_to(repo.root): path.read_bytes()
            for path in repo.root.rglob("*")
            if path.is_file()
        }

    def rehearse(delay: int, *flags: str):
        run = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/check_encouragement_capacity.py"),
                "--workspace",
                str(repo.root),
                "--seed",
                "7",
                "--confirmation-delay-minutes",
                str(delay),
                "--time-limit-seconds",
                "10",
                *flags,
            ],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True,
            text=True,
            check=False,
        )
        assert not run.stderr
        assert "Player " not in run.stdout
        return run.returncode, json.loads(run.stdout)

    before = workspace_files()
    code, report = rehearse(10)
    assert code == 0 and report["status"] == "feasible"
    assert report["source_revision"] == stored.id
    assert report["original_assignments_preserved"]
    assert report["encouragement_matches"] == 3
    division = report["divisions"][0]
    assert division["eligible_players"] == 4 and len(division["court_windows"]) == 3
    assert all(
        w["start"] >= division["earliest_encouragement_start"] for w in division["court_windows"]
    )
    code, report = rehearse(10_000)
    assert code == 1 and report["status"] == "infeasible"
    assert report["divisions"][0]["court_windows"] == []
    code, report = rehearse(10, "--replan-main")
    assert code == 0 and report["status"] == "feasible"
    assert report["frozen_main_replay_verified"] and report["total_matches"] == 10
    assert workspace_files() == before
