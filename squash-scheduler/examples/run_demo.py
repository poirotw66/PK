"""Generate anonymous inputs and run the real CLI from Word to a DRAFT PDF.

Run with the project's Python: python examples/run_demo.py --output output/demo.
Every event date, duration and court in this example is synthetic.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from docx import Document

from squash_scheduler.domain.models import stable_id
from squash_scheduler.storage.repository import Repository


def run_demo(output: Path, *, pdf: bool = True) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    template = root.parent / "squash-event-config.template.json"
    config = json.loads(template.read_text("utf-8-sig"))
    division_id = stable_id("synthetic", "mens-f")
    config["event"] = {
        "id": stable_id("synthetic", "event"),
        "name": "匿名男子 F 組原型（合成資料）",
        "timezone": "Asia/Taipei",
        "event_dates": ["2030-01-01"],
        "venue_name": "合成測試場館",
        "actor_id": "synthetic-operator",
    }
    config["divisions_status"] = "configured"
    config["divisions"] = [
        {
            "id": division_id,
            "name": "男子 F 組",
            "category": "competitive",
            "gender_category": "male",
            "level_order": 6,
            "minimum_entries": 3,
            "status": "active",
            "format_plan": {
                "template_id": "pools_3x3_top2_knockout6_bronze",
                "template_version": "1.0.0",
                "pool_sizes": [3, 3, 3],
                "advancement_counts": [2, 2, 2],
                "bracket_capacity": 8,
                "placement_matches": ["bronze"],
                "status": "draft",
                "approval_id": None,
            },
            "rule_overrides": {},
            "draw_overrides": {},
        }
    ]
    config["registration"]["division_aliases"] = {"男子F組": division_id, "男子 F 組": division_id}
    config["merge_whitelist_status"] = "configured"
    config["courts_status"] = "configured"
    config["courts"] = [
        {
            "id": stable_id("synthetic", f"court-{i}"),
            "label": f"合成球場 {i}",
            "sort_order": i,
            "availability": [
                {"start_at": "2030-01-01T08:00:00+08:00", "end_at": "2030-01-01T20:00:00+08:00"}
            ],
            "blackouts": [
                {"start_at": "2030-01-01T12:00:00+08:00", "end_at": "2030-01-01T13:00:00+08:00"}
            ],
        }
        for i in (1, 2)
    ]
    config["timing_defaults"] = {
        "start_grid_minutes": 15,
        "daily_grid_anchors": [{"date": "2030-01-01", "local_time": "08:00"}],
        "estimated_play_minutes": 25,
        "court_buffer_minutes": 5,
        "minimum_player_rest_minutes": 20,
        "court_transfer_minutes": 0,
        "ranking_confirmation_minutes": 10,
    }
    config["draw_defaults"].update(
        seeded_entries_mode="none", affiliation_avoidance_mode="disabled"
    )
    config["publication"].update(qr_mode="disabled", sponsor_mode="none")
    config_path = output / "event.synthetic.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
    document = Document()
    document.add_paragraph("男子 F 組")
    for i in range(1, 10):
        document.add_paragraph(f"{i}. 匿名選手{i:02}")
    roster = output / "roster.synthetic.docx"
    document.save(roster)
    workspace = output / "tournament"
    commands = []

    def cli(*args: str) -> dict:
        invocation = [*args, "--workspace", str(workspace), "--json"]
        completed = subprocess.run(
            [sys.executable, "-m", "squash_scheduler", *invocation],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        if completed.returncode:
            raise RuntimeError(
                f"CLI {' '.join(args)} exited {completed.returncode}: {completed.stdout} {completed.stderr}"
            )
        result = json.loads(completed.stdout)
        commands.append({"args": list(args), "exit_code": completed.returncode})
        return result

    cli("init", "--config", str(config_path))
    report = cli("roster", "import", "--file", str(roster))
    current = Repository(workspace).load()
    batch_id = current.imports[0]["id"]
    resolutions = [
        {
            "candidate_id": c["id"],
            "create_participant": {
                "official_name": c["official_name"],
                "display_name": c["official_name"],
            },
        }
        for c in current.imports[0]["candidates"]
        if c["status"] != "ignored_with_reason"
    ]
    patch = {
        "base_revision_id": current.id,
        "batch_id": batch_id,
        "actor_id": "synthetic-operator",
        "reason": "確認匿名測試身分；非真實名單",
        "resolutions": resolutions,
    }
    patch_path = output / "identity-patch.synthetic.json"
    patch_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2), "utf-8")
    cli("roster", "resolve", "--patch", str(patch_path))
    review = cli("review", "build")
    target = next(t for t in review["approval_targets"] if t["target"].startswith("format:"))
    cli(
        "review",
        "approve",
        "--target",
        target["target"],
        "--hash",
        target["hash"],
        "--reason",
        "僅核准合成賽制",
    )
    draw = cli("draw", "run", "--division", division_id, "--seed", "20261027")
    cli(
        "review",
        "approve",
        "--target",
        "draw:" + draw["draw"]["id"],
        "--hash",
        draw["approval_hash"],
        "--reason",
        "僅核准合成抽籤",
    )
    cli("matches", "generate")
    solved = cli("schedule", "solve", "--time-limit", "20")
    checked = cli("validate", "--gate", "G4")
    cli("review", "build")
    preview = cli("export", "preview") if pdf else None
    snapshot = Repository(workspace).load()
    snapshot_path = output / "nine-entries.snapshot.json"
    snapshot_path.write_text(snapshot.model_dump_json(indent=2), "utf-8")
    summary = {
        "synthetic_only": True,
        "participants": len(snapshot.participants),
        "matches": len(snapshot.matches),
        "solver_status": solved["status"],
        "g4_passed": checked["passed"],
        "workspace": str(workspace),
        "preview": preview["path"] if preview else None,
        "commands": commands,
        "import_rows": report.get("counts", {}),
    }
    (output / "demo-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), "utf-8"
    )
    return summary


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--output", type=Path, default=Path("output/demo"))
    argument_parser.add_argument("--no-pdf", action="store_true")
    args = argument_parser.parse_args()
    print(
        json.dumps(
            run_demo(args.output.resolve(), pdf=not args.no_pdf), ensure_ascii=False, indent=2
        )
    )
