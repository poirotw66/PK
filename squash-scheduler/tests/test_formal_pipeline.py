"""Actual CLI formal build and explicit synthetic-only QA finalization integration."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from squash_scheduler.domain.models import canonical_hash
from squash_scheduler.exporters.releases import VISUAL_CHECKS
from squash_scheduler.storage.repository import Repository


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PDF_SMOKE") != "1",
    reason="Set SQUASH_PDF_SMOKE=1 for real CLI/Edge/font pipeline",
)
def test_real_cli_build_and_explicit_synthetic_qa_finalization_preserve_pending_bundle(tmp_path):
    font = Path("C:/Windows/Fonts/msjh.ttc")
    if not font.is_file():
        pytest.skip("Local Traditional Chinese integration font is unavailable")
    script = Path(__file__).parents[1] / "examples" / "run_formal_demo.py"
    spec = importlib.util.spec_from_file_location("formal_synthetic_demo", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_formal_demo(tmp_path / "formal", font)
    assert report["status"] == "qa_pending" and report["synthetic_only"]
    repo = Repository(Path(report["workspace"]))
    assert repo.load(report["approved_revision_id"]).status == "approved"
    template = json.loads(Path(report["visual_review_template"]).read_text("utf-8"))
    assert template["reviewed_pages"] == [] and not any(template["checks"].values())
    pending = Path(report["path"])
    manifest_bytes = (pending / "manifest.json").read_bytes()
    old_manifest = json.loads(manifest_bytes)
    assert report["manifest_hash"] == canonical_hash(old_manifest)
    pointer = repo.root / "releases" / "latest-ready.json"
    assert not pointer.exists()

    def finalize(path):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "squash_scheduler",
                "release",
                "finalize",
                "--workspace",
                str(repo.root),
                "--file",
                str(path),
                "--actor",
                "synthetic-integration-reviewer",
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )

    incomplete_path = tmp_path / "unchecked.synthetic.json"
    incomplete_path.write_text(
        json.dumps(
            {
                **template,
                "actor_id": "synthetic-integration-reviewer",
                "reason": "Synthetic test only: unchecked pages must block readiness",
            }
        ),
        "utf-8",
    )
    assert finalize(incomplete_path).returncode == 4
    assert not pointer.exists()
    review = {
        **template,
        "actor_id": "synthetic-integration-reviewer",
        "reason": "SYNTHETIC TEST ONLY: exercise the explicit QA finalization contract; no real event approvals",
        "reviewed_pages": [page["page"] for page in report["pages"]],
        "checks": dict.fromkeys(VISUAL_CHECKS, True),
    }
    completed_path = tmp_path / "visual-review.synthetic-test-only.json"
    completed_path.write_text(json.dumps(review), "utf-8")
    completed = finalize(completed_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    ready = json.loads(completed.stdout)
    assert ready["status"] == "ready" and ready["release_id"] != report["release_id"]
    assert (pending / "manifest.json").read_bytes() == manifest_bytes
    assert json.loads(pointer.read_text("utf-8"))["release_id"] == ready["release_id"]
    ready_folder = Path(ready["path"])
    new_manifest = json.loads((ready_folder / "manifest.json").read_text("utf-8"))
    assert new_manifest["parent_release_id"] == report["release_id"]
    for name, digest in old_manifest["artifact_hashes"].items():
        assert hashlib.sha256((pending / name).read_bytes()).hexdigest() == digest
        assert hashlib.sha256((ready_folder / name).read_bytes()).hexdigest() == digest
