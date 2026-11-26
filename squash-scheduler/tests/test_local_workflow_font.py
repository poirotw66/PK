"""Controlled font selection produces a fresh draft and preserves version checks."""

from pathlib import Path

import pytest
from test_local_workflow import setup

from squash_scheduler.local_workflow import LocalWorkflow
from squash_scheduler.operations import OperatorService
from squash_scheduler.storage.repository import Repository


@pytest.fixture
def workflow(tmp_path):
    service = OperatorService(Repository(tmp_path / "font-test"))
    yield LocalWorkflow(service)
    service.close()


def font_bytes():
    for path in (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if path.is_file():
            return path.read_bytes()
    pytest.skip("No test font installed")


def test_font_import_creates_audited_draft_with_content_addressed_asset(workflow):
    snapshot, _ = setup(workflow)
    with workflow.service.repo.lock():
        snapshot.status = "approved"
        snapshot = workflow.service.repo.save(snapshot, snapshot.id)
    result = workflow.font(
        font_bytes(), {"revision_id": snapshot.id, "actor": "synthetic-operator"}
    )
    current = workflow.service.repo.load()
    assert current.id == result["revision_id"] != snapshot.id
    assert current.status == "draft"
    assert current.config["publication"]["font_asset_id"] == result["asset"]["asset_id"]
    assert current.changes[-1]["kind"] == "font_import"
    assert (
        workflow.service.repo.root / "assets" / (result["asset"]["asset_id"] + ".bin")
    ).is_file()


def test_stale_font_upload_cannot_change_configuration(workflow):
    current, _ = setup(workflow)
    with pytest.raises(ValueError, match="版本已更新"):
        workflow.font(b"invalid font", {"revision_id": "stale", "actor": "synthetic-operator"})
    assert workflow.service.repo.load().id == current.id
    assert workflow.service.repo.load().config["publication"]["font_asset_id"] is None
