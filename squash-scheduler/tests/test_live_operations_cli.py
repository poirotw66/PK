"""Exercise reviewed live operations through actual repository/CLI boundaries."""

import importlib.util
import json
from pathlib import Path

from test_withdrawals import configure_walkovers

from squash_scheduler.cli import main
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import DrawSlotSide
from squash_scheduler.storage.repository import Repository


def workspace(tmp_path):
    script = Path(__file__).parents[1] / "examples/run_demo.py"
    spec = importlib.util.spec_from_file_location("live_operations_demo", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_demo(tmp_path / "demo", pdf=False)
    repo = Repository(Path(report["workspace"]))
    with repo.lock():
        snapshot = repo.load()
        configure_walkovers(snapshot)
        generate_matches(snapshot)
        snapshot = repo.save(snapshot, snapshot.id)
    return repo, snapshot


def command(repo, capsys, *args):
    capsys.readouterr()
    code = main([*args, "--workspace", str(repo.root), "--actor", "operator", "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_cli_withdraw_review_and_replan_are_real_revision_operations(tmp_path, capsys):
    repo, original = workspace(tmp_path)
    code, listed = command(repo, capsys, "entries", "list")
    assert code == 0 and len(listed["entries"]) == 9
    assert listed["entries"][0]["entry_id"] == original.entries[0].id
    proposal_path = tmp_path / "withdraw.json"
    code, proposed = command(
        repo,
        capsys,
        "entries",
        "propose",
        "--entry",
        original.entries[0].id,
        "--kind",
        "withdraw",
        "--reason",
        "DEMO injury",
        "--output",
        str(proposal_path),
    )
    assert code == 0 and proposed["dry_run"]
    assert repo.load().id == original.id
    code, applied = command(repo, capsys, "entries", "apply", "--file", str(proposal_path))
    assert code == 0 and applied["impact"]["walkover_match_ids"]
    assert repo.load().id != original.id
    before_review_id = repo.load().id
    code, _rejected = command(
        repo,
        capsys,
        "schedule",
        "replan",
        "--change",
        applied["change_id"],
        "--time-limit",
        "10",
    )
    assert code != 0
    assert repo.load().id == before_review_id
    code, _ = command(
        repo,
        capsys,
        "review",
        "approve",
        "--target",
        "entry_change:" + applied["change_id"],
        "--hash",
        applied["approval_hash"],
        "--reason",
        "Reviewed withdrawal impact",
    )
    assert code == 0
    code, replanned = command(
        repo,
        capsys,
        "schedule",
        "replan",
        "--change",
        applied["change_id"],
        "--time-limit",
        "10",
    )
    assert code == 0 and replanned["status"] in {"OPTIMAL", "FEASIBLE"}
    assert {m.id: m.display_code for m in repo.load().matches} == {
        m.id: m.display_code for m in original.matches
    }
    assert repo.load(original.id).entries[0].status == "active"
    code, _ = command(repo, capsys, "entries", "apply", "--file", str(proposal_path))
    assert code != 0  # stale proposal cannot apply twice


def test_cli_deferred_declaration_execution_and_activation_require_real_approvals(tmp_path, capsys):
    repo, original = workspace(tmp_path)
    code, listed = command(repo, capsys, "deferred-draw", "list")
    assert code == 0 and listed["draws"] == []
    sources = [{"type": "entry", "entry_id": e.id} for e in original.entries[:2]]
    declaration = {
        "division_id": original.divisions[0].id,
        "slots": [{"slot_id": str(i), "candidate_sides": sources} for i in (1, 2)],
        "procedure": {
            "description": "DEMO witnessed bag draw",
            "execution_trigger": "after_sources_confirmed",
            "duration_minutes": 5,
            "earliest_at": "2030-01-01T08:00:00+08:00",
        },
    }
    file = tmp_path / "declare.json"
    file.write_text(json.dumps(declaration), "utf-8")
    code, proposed = command(
        repo,
        capsys,
        "deferred-draw",
        "propose",
        "--file",
        str(file),
        "--reason",
        "DEMO onsite draw",
    )
    assert code == 0
    parent_id = proposed["draw"]["id"]
    code, _ = command(
        repo,
        capsys,
        "review",
        "approve",
        "--target",
        "deferred_draw:" + parent_id,
        "--hash",
        proposed["approval_hash"],
        "--reason",
        "Review candidates and procedure",
    )
    assert code == 0
    stage_ids = {s.id for s in original.stages if s.type == "elimination"}
    match = next(m for m in original.matches if m.stage_id in stage_ids)
    file.write_text(
        json.dumps(
            {
                "draw_run_id": parent_id,
                "bindings": [
                    {"match_id": match.id, "side": field, "slot_id": str(i)}
                    for i, field in enumerate(("side_a", "side_b"), 1)
                ],
            }
        ),
        "utf-8",
    )
    code, _ = command(
        repo,
        capsys,
        "deferred-draw",
        "bind",
        "--file",
        str(file),
        "--reason",
        "Bind reviewed draw slots",
    )
    assert code == 0
    file.write_text(
        json.dumps(
            {
                "draw_run_id": parent_id,
                "assignments": [
                    {"slot_id": str(i), "entry_id": entry.id}
                    for i, entry in enumerate(original.entries[:2], 1)
                ],
                "executed_at": "2030-01-01T08:05:00+08:00",
                "evidence": "DEMO signed sheet",
            }
        ),
        "utf-8",
    )
    code, performed = command(
        repo,
        capsys,
        "deferred-draw",
        "execute",
        "--file",
        str(file),
        "--reason",
        "Record witnessed execution",
    )
    assert code == 0
    execution_id = performed["draw"]["id"]
    code, _ = command(
        repo,
        capsys,
        "deferred-draw",
        "activate",
        "--execution",
        execution_id,
        "--reason",
        "Activate execution",
    )
    assert code != 0
    code, _ = command(
        repo,
        capsys,
        "review",
        "approve",
        "--target",
        "deferred_draw:" + execution_id,
        "--hash",
        performed["approval_hash"],
        "--reason",
        "Review signed draw sheet",
    )
    assert code == 0
    code, _ = command(
        repo,
        capsys,
        "deferred-draw",
        "activate",
        "--execution",
        execution_id,
        "--reason",
        "Activate reviewed execution",
    )
    assert code == 0
    revised = next(m for m in repo.load().matches if m.id == match.id)
    assert isinstance(revised.side_a, DrawSlotSide) and revised.side_a.draw_run_id == execution_id
    assert revised.display_code == match.display_code
