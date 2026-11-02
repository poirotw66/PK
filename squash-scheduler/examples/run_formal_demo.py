"""Build a synthetic, controlled-font QA bundle through the actual CLI.

No visual checks are approved by this script and it never finalizes a release.
Run: python examples/run_formal_demo.py --output output/formal-demo
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from squash_scheduler.domain.models import canonical_hash, content_hash
from squash_scheduler.exporters.releases import VISUAL_CHECKS
from squash_scheduler.storage.repository import Repository


def run_formal_demo(output: Path, font_path: Path) -> dict:
    """Create a fresh anonymous event, explicitly approve its synthetic rules, and stop at QA."""
    output, font_path = output.resolve(), font_path.resolve()
    if not font_path.is_file():
        raise ValueError("The formal demo requires an existing local Traditional Chinese font")
    script = Path(__file__).with_name("run_demo.py")
    spec = importlib.util.spec_from_file_location("synthetic_seed_demo", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the anonymous input demo")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = module.run_demo(output, pdf=False)
    workspace = Path(baseline["workspace"])
    repo = Repository(workspace)
    commands = list(baseline["commands"])

    def cli(*arguments: str, expected_exit: int = 0) -> dict:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "squash_scheduler",
                *arguments,
                "--workspace",
                str(workspace),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        if result.returncode != expected_exit:
            raise RuntimeError(
                f"CLI {' '.join(arguments)} exited {result.returncode}, expected {expected_exit}: {result.stdout} {result.stderr}"
            )
        commands.append({"args": list(arguments), "exit_code": result.returncode})
        return json.loads(result.stdout)

    asset = cli("assets", "import", "--kind", "font", "--file", str(font_path))
    snapshot = repo.load()
    # These are demonstration choices only; no historical or real-event rules are inferred.
    rules = {
        "best_of_games": 3,
        "points_to_win_game": 11,
        "win_by_points": 2,
        "point_cap_mode": "none",
        "point_cap": None,
        "late_arrival_rule": {
            "grace_minutes": 5,
            "action": "manual_review",
            "synthetic_only": True,
        },
        "walkover_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "winner_points": 2,
            "loser_points": 0,
            "synthetic_only": True,
        },
        "retirement_rule": {
            "include_in_rankings": True,
            "counts_as_played": True,
            "winner_points": 2,
            "loser_points": 0,
            "synthetic_only": True,
        },
        "double_forfeit_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "side_a_points": 0,
            "side_b_points": 0,
            "synthetic_only": True,
        },
        "replacement_rule": {"mode": "manual_review", "automatic": False, "synthetic_only": True},
        "ranking_denominator_rule": {"walkover": True, "retired": True, "double_forfeit": True},
        "multiway_tie_rule": {"mode": "manual_review", "synthetic_only": True},
    }
    publication = {
        **snapshot.config["publication"],
        "layout_template_id": "standard-v1",
        "paper_size": "A4",
        "orientation": "landscape",
        "font_asset_id": asset["asset_id"],
        "qr_mode": "enabled",
        "qr_url": "https://example.test/synthetic",
        "qr_label": "合成測試通知網址（非真實賽事）",
        "sponsor_mode": "none",
        "sponsors": [],
    }
    patch = {
        "base_revision_id": snapshot.id,
        "actor_id": "synthetic-operator",
        "reason": "僅設定合成測試比賽規則與本機字型；不核准任何真實賽事",
        "operations": [
            {"op": "set_config", "field": "match_rule_defaults", "value": rules},
            {"op": "set_config", "field": "publication", "value": publication},
        ],
    }
    patch_path = output / "formal-config-patch.synthetic.json"
    patch_path.write_text(json.dumps(patch, ensure_ascii=False, indent=2), "utf-8")
    cli("changes", "apply", "--file", str(patch_path))
    validation = cli("validate", "--gate", "G5")
    if not validation["passed"] or validation["warnings"]:
        raise RuntimeError("The synthetic formal configuration did not pass G5 without warnings")
    review = cli("review", "build")
    current = repo.load()
    revision_target = next(
        t for t in review["approval_targets"] if t["target"] == "revision:" + current.id
    )
    if revision_target["hash"] != content_hash(current):
        raise RuntimeError("Revision approval target is stale")
    approval = cli(
        "review",
        "approve",
        "--target",
        revision_target["target"],
        "--hash",
        revision_target["hash"],
        "--reason",
        "僅核准合成範例快照供系統測試；非真實賽事核准",
    )
    approved_id = approval["revision_id"]
    approved = repo.load(approved_id)
    if approved.status != "approved" or approval["approval"]["target_id"] != approved_id:
        raise RuntimeError("Approval does not target the exact stored immutable revision")
    release = cli("release", "build", "--revision", approved_id, expected_exit=4)
    if release["status"] != "qa_pending":
        raise RuntimeError("The formal demonstration must stop for visual QA")
    folder = Path(release["path"])
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    pages = [
        {**page, "preview_path": str(folder / page["preview"])} for page in manifest["qa"]["pages"]
    ]
    if not pages or not all(Path(page["preview_path"]).is_file() for page in pages):
        raise RuntimeError("The QA bundle does not include every rendered page")
    review_template = {
        "release_id": release["release_id"],
        "manifest_hash": canonical_hash(manifest),
        "actor_id": "synthetic-operator",
        "reason": "",
        "reviewed_pages": [],
        "checks": dict.fromkeys(sorted(VISUAL_CHECKS), False),
        "synthetic_only": True,
    }
    template_path = output / "visual-review.template.json"
    template_path.write_text(json.dumps(review_template, ensure_ascii=False, indent=2), "utf-8")
    report = {
        "synthetic_only": True,
        "status": "qa_pending",
        "workspace": str(workspace),
        "approved_revision_id": approved_id,
        "approval_source_revision_id": current.id,
        "release_id": release["release_id"],
        "path": str(folder),
        "manifest_hash": canonical_hash(manifest),
        "page_count": len(pages),
        "pages": pages,
        "font_asset_id": asset["asset_id"],
        "g5_passed": validation["passed"],
        "visual_review_template": str(template_path),
        "commands": commands,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/formal-demo"))
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msjh.ttc"))
    arguments = parser.parse_args()
    print(
        json.dumps(run_formal_demo(arguments.output, arguments.font), ensure_ascii=False, indent=2)
    )
