"""A complete synthetic prospectus can reach real CLI/Edge QA without human sign-off."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from squash_scheduler.domain.models import content_hash
from squash_scheduler.exporters.pdf_navigation import validate_navigation
from squash_scheduler.exporters.prospectus import SECTION_FIELDS
from squash_scheduler.storage.repository import Repository


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PDF_SMOKE") != "1",
    reason="Set SQUASH_PDF_SMOKE=1 for the real formal prospectus CLI/Edge/font pipeline",
)
def test_formal_v2_cli_pipeline(tmp_path):
    font = Path("C:/Windows/Fonts/msjh.ttc")
    if not font.is_file():
        pytest.skip("Local Traditional Chinese integration font is unavailable")
    script = Path(__file__).parents[1] / "examples" / "run_formal_demo.py"
    spec = importlib.util.spec_from_file_location("formal_prospectus_seed", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Keep the test fixture below Windows' legacy path limit when pytest adds
    # its descriptive temporary-folder name to the release bundle path.
    seed = module.run_formal_demo(tmp_path / "s", font)
    assert seed["status"] == "qa_pending" and seed["synthetic_only"]
    repo = Repository(Path(seed["workspace"]))
    pointer = repo.root / "releases" / "latest-ready.json"
    assert not pointer.exists()

    def cli(*arguments: str, expected_exit: int = 0) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "squash_scheduler",
                *arguments,
                "--workspace",
                str(repo.root),
                "--actor",
                "synthetic-operator",
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=240,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        assert completed.returncode == expected_exit, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    # These invented clauses are approved only inside this isolated synthetic
    # fixture. They neither derive from nor approve a real event's regulations.
    clauses = {
        "organizers": ["主辦：虛構壁球測試協會，僅供合成流程驗證。"],
        "co_organizers": ["協辦：虛構測試運動中心，提供本次合成賽事場地。"],
        "eligibility": ["參賽者為本測試資料中已完成報名及組別核定的虛構選手。"],
        "registration": ["合成報名於測試賽日前七日截止，報名費為零元。"],
        "check_in": ["虛構選手於首場比賽前三十分鐘至測試服務台報到。"],
        "competition": ["本合成測試依已核准的組別對戰圖、每日時間表及本冊比賽規則進行。"],
        "awards": ["合成各組前三名頒發虛構電子獎狀，無實體獎品及獎金。"],
        "appeals": ["測試申訴須於該場結束後十五分鐘內向虛構裁判長提出，由其作成測試決定。"],
        "contact": ["合成流程聯絡信箱：prospectus@example.test。"],
    }
    assert set(clauses) == {field for field, _ in SECTION_FIELDS}
    current = repo.load()
    patch_path = tmp_path / "prospectus-config.synthetic.json"
    patch_path.write_text(
        json.dumps(
            {
                "base_revision_id": current.id,
                "actor_id": "synthetic-operator",
                "reason": "僅核准完整虛構簡章供系統測試；不涉及任何真實賽事條款或人工版面簽核",
                "operations": [
                    {
                        "op": "set_config",
                        "field": "publication",
                        "value": {
                            **current.config["publication"],
                            "layout_template_id": "reference-v2",
                            "document_profile": "prospectus",
                            "prospectus": {**clauses, "approval_status": "approved"},
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )
    cli("changes", "apply", "--file", str(patch_path))
    validation = cli("validate", "--gate", "G5")
    assert validation["passed"] and not validation["warnings"]
    review = cli("review", "build")
    current = repo.load()
    target = next(
        item for item in review["approval_targets"] if item["target"] == "revision:" + current.id
    )
    assert target["hash"] == content_hash(current)
    approved = cli(
        "review",
        "approve",
        "--target",
        target["target"],
        "--hash",
        target["hash"],
        "--reason",
        "僅核准此完整合成簡章快照供整合測試；人工逐頁審查仍未執行",
    )
    snapshot = repo.load(approved["revision_id"])
    assert snapshot.status == "approved"
    assert approved["approval"]["target_id"] == snapshot.id
    assert approved["approval"]["actor_id"] == "synthetic-operator"
    result = cli("release", "build", "--revision", snapshot.id, expected_exit=4)
    assert result["status"] == "qa_pending"
    folder = Path(result["path"])
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    model = json.loads((folder / "render-model.json").read_text("utf-8"))
    navigation = json.loads((folder / "navigation-map.json").read_text("utf-8"))
    html = (folder / "review.html").read_text("utf-8")
    assert manifest["status"] == "qa_pending" and manifest["qa"]["passed"]
    assert manifest["qa"]["navigation"]["passed"]
    assert model["template"]["id"] == "reference-v2"
    assert model["draft"] is False
    assert model["prospectus"]["approved"] and model["prospectus"]["complete"]
    assert model["prospectus"]["missing_fields"] == []
    assert {section["field"] for section in model["prospectus"]["sections"]} == set(clauses)
    assert validate_navigation(folder / "booklet.pdf", html, navigation) == navigation
    assert {"prospectus", "rules"} <= {entry["target"] for entry in navigation["entries"]}

    with pymupdf.open(folder / "booklet.pdf") as pdf:
        assert len(pdf) == navigation["page_count"] == len(manifest["qa"]["pages"])
        assert pdf.get_toc() == [
            [entry["level"], entry["title"], entry["page"]] for entry in navigation["entries"]
        ]
        printed = "".join(pdf[index].get_text() for index in range(len(pdf)))
    normalized = "".join(printed.split())
    for section in model["prospectus"]["sections"]:
        assert section["lines"] == clauses[section["field"]]
        for line in section["lines"]:
            assert "".join(line.split()) in normalized
    for summary in model["rule_summaries"]:
        for line in summary["lines"]:
            assert "".join(line.split()) in normalized
    for forbidden in ("待補", "demo_only", "synthetic_only", "grace_minutes", "automatic"):
        assert forbidden not in printed
    # Release build must stop for actual human page review, even after the
    # synthetic snapshot and its complete content have been explicitly approved.
    assert not pointer.exists()
