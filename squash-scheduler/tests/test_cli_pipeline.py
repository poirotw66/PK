"""Exercise actual command boundaries, UTF-8 JSON, revision gates and the Word pipeline."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from squash_scheduler.cli import main
from squash_scheduler.domain.models import Snapshot
from squash_scheduler.domain.validation import validate
from squash_scheduler.storage.repository import Repository


def test_real_cli_word_to_independently_validated_schedule(tmp_path):
    script = Path(__file__).parents[1] / "examples" / "run_demo.py"
    spec = importlib.util.spec_from_file_location("synthetic_demo", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_demo(tmp_path / "demo", pdf=False)
    assert report["participants"] == 9 and report["matches"] == 15
    assert report["g4_passed"] and report["solver_status"] in ("FEASIBLE", "OPTIMAL")
    repo = Repository(Path(report["workspace"]))
    snapshot = repo.load()
    assert len(snapshot.registrations) == 9 and len(snapshot.assignments) == 15
    assert len(snapshot.pools) == 3
    assert validate(snapshot, "G4")["passed"]
    # Synthetic dates/courts permit a draft schedule, not an official rules publication.
    assert not validate(snapshot, "G5")["passed"]
    original_count = len(snapshot.registrations)
    assert (
        main(
            [
                "roster",
                "import",
                "--file",
                str(tmp_path / "demo" / "roster.synthetic.docx"),
                "--workspace",
                str(repo.root),
                "--json",
            ]
        )
        == 0
    )
    assert len(repo.load().registrations) == original_count
    for source in snapshot.sources:
        assert (repo.root / "source" / source["id"] / "original").is_file()


def test_template_load_preserves_unknowns_and_blocks_later_operations(tmp_path, capsys):
    template = Path(__file__).parents[2] / "squash-event-config.template.json"
    workspace = tmp_path / "empty"
    assert (
        main(
            [
                "init",
                "--config",
                str(template),
                "--workspace",
                str(workspace),
                "--actor",
                "tester",
                "--json",
            ]
        )
        == 0
    )
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["status"] == "draft" and "timezone" in initialized["missing_parameters"]
    snapshot = Repository(workspace).load()
    assert snapshot.config["event"]["event_dates"] is None
    assert snapshot.config["timing_defaults"]["minimum_player_rest_minutes"] is None
    assert main(["validate", "--gate", "G4", "--workspace", str(workspace), "--json"]) == 3
    assert not json.loads(capsys.readouterr().out)["passed"]


def test_init_requires_actor_and_never_overwrites_workspace(tmp_path, capsys):
    template = Path(__file__).parents[2] / "squash-event-config.template.json"
    args = ["init", "--config", str(template), "--workspace", str(tmp_path / "event"), "--json"]
    assert main(args) == 3
    assert "actor" in json.loads(capsys.readouterr().out)["message"]
    assert main([*args, "--actor", "tester"]) == 0
    capsys.readouterr()
    repo = Repository(tmp_path / "event")
    before = repo.load().id
    assert main([*args, "--actor", "tester"]) == 8
    assert repo.load().id == before


def test_unknown_schema_rejected_and_schema_discriminator_emitted(tmp_path):
    schema_dir = tmp_path / "schemas"
    assert main(["schema", "--output", str(schema_dir), "--json"]) == 0
    schema = json.loads((schema_dir / "snapshot.schema.json").read_text("utf-8"))
    assert (
        schema["$defs"]["Match"]["properties"]["side_a"]["discriminator"]["propertyName"] == "type"
    )
    with pytest.raises(ValueError):
        Snapshot(config={}, schema_version="2.0.0")
