"""Create isolated operator workspaces from already archived demo evidence."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from squash_scheduler.domain.models import Snapshot
from squash_scheduler.exporters.assets import load_asset
from squash_scheduler.storage.repository import Repository, atomic_json


def build(destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError("Choose a fresh operator demo directory")
    root = Path(__file__).resolve().parents[1]
    source = root / "output/consolation-reviewed-workflow/tournament"
    candidates = []
    for file in (source / "revisions").glob("*/snapshot.json"):
        value = json.loads(file.read_text("utf-8"))["snapshot"]
        if len(value["matches"]) == 8 and len(value["assignments"]) == 8 and not value["results"]:
            candidates.append(Snapshot.model_validate(value))
    if not candidates:
        raise ValueError("Build the reviewed consolation workflow first")
    junior = max(candidates, key=lambda value: value.created_at)
    real_source = root / "output/full-demo-four-courts/tournament"
    full = Repository(real_source).load()
    destination.mkdir(parents=True)
    result = {}
    for label, snapshot, assets_source, name in [
        ("junior", junior, source, "虛構國中組 · 賽務操作示範"),
        ("full-roster", full, real_source, "中正盃原始名單 · 賽務操作示範"),
    ]:
        working = snapshot.model_copy(deep=True)
        working.status = "draft"
        working.config["event"]["name"] = name
        working.config["event"]["actor_id"] = "Justin"
        working.config["publication"]["layout_template_id"] = "reference-v2"
        if label == "full-roster":
            working.config["publication"].update(
                document_profile="prospectus",
                prospectus={"approval_status": "draft"},
                draft_note="原始名單操作示範；非正式核准賽程，條文保留待補",
            )
        for court in working.config.get("courts", []):
            if court["label"] in {"A", "B", "C", "D"}:
                court["label"] = "場地" + court["label"]
        workspace = destination / label
        repo = Repository(workspace)
        with repo.lock():
            stored = repo.save(working, None)
        for directory in ("assets", "source"):
            source_directory = assets_source / directory
            if directory == "assets":
                if not source_directory.is_dir():
                    # The older full-roster repository stores its print assets in
                    # the original delivery wrapper, not beside working/current.json.
                    source_directory = root / "output/full-roster-simulation-delivery/assets"
                font_id = working.config["publication"].get("font_asset_id")
                if font_id:
                    load_asset(source_directory, font_id, "font")
            if source_directory.is_dir():
                shutil.copytree(source_directory, workspace / directory)
        result[label] = {
            "workspace": str(workspace.resolve()),
            "revision_id": stored.id,
            "entries": len(stored.entries),
            "matches": len(stored.matches),
        }
    atomic_json(destination / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/operator-demo"))
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2, ensure_ascii=True))
