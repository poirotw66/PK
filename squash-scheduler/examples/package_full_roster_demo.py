"""Package an already validated real-roster simulation with current UI and CLI workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from squash_scheduler.domain.models import Snapshot
from squash_scheduler.domain.validation import validate
from squash_scheduler.exporters.documents import render_html, view_model
from squash_scheduler.exporters.portal import write_portal
from squash_scheduler.storage.repository import Repository, atomic_json


def package(source: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh demo output directory")
    snapshot = Snapshot.model_validate_json((source / "snapshot.json").read_text("utf-8"))
    report = json.loads((source / "report.json").read_text("utf-8"))
    if not report["g4_passed"] or not validate(snapshot, "G4")["passed"]:
        raise ValueError("The source simulation must pass G4")
    output.mkdir(parents=True)
    project = Path(__file__).resolve().parents[1]
    repo = Repository(output / "tournament")
    with repo.lock():
        for original in snapshot.sources:
            file = project / "output/historical-roster-ready/roster.converted.docx"
            repo.archive_source(file, original)
        snapshot = repo.save(snapshot, None, revision_id=snapshot.id)
    for filename in ("booklet.pdf", "schedule.xlsx"):
        shutil.copyfile(source / "draft" / filename, output / filename)
    for filename in ("solver-report.json", "g4-report.json", "assumptions.json"):
        shutil.copyfile(source / filename, output / filename)
    model = view_model(snapshot)
    model["public_name"] = "中正盃 · 四場地模擬編排"
    model["coverage_note"] = (
        "完整帶入 147 筆報名、114 位選手與 17 組，場地A～D為使用者確認的四個場地。"
        "此版本重新抽籤與排程；賽制、08:00–22:00開放、10分鐘比賽＋5分鐘緩衝、"
        "15分鐘休息均為DEMO假設，不代表原公布賽程或正式核准。三個單人組保留名單，不虛構對手。"
    )
    (output / "review.html").write_text(
        render_html(model, output, asset_root=source / "assets"), "utf-8"
    )
    atomic_json(output / "render-model.json", model)
    write_portal(model, output)
    result = {
        **report,
        "path": str(output.resolve()),
        "workspace": str(repo.root),
        "source_package": str(source.resolve()),
        "source_pdf_preserved": True,
        "artifacts": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in output.iterdir()
            if path.is_file()
        },
    }
    atomic_json(output / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("output/full-roster-simulation-delivery")
    )
    parser.add_argument("--output", type=Path, default=Path("output/full-demo-four-courts"))
    args = parser.parse_args()
    result = package(args.source, args.output)
    print(
        json.dumps(
            {key: result[key] for key in ("path", "workspace", "matches", "g4_passed")},
            ensure_ascii=False,
        )
    )
