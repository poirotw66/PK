"""The product example uses typed domain definitions, never source-only graph dicts."""

import importlib.util
from pathlib import Path

from squash_scheduler.domain.config import EventConfig
from squash_scheduler.exporters.prospectus import publication_content_issues


def demo_module():
    path = Path(__file__).resolve().parents[1] / "examples/build_consolation_demo.py"
    spec = importlib.util.spec_from_file_location("consolation_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fictional_configuration_does_not_preassign_unconfirmed_first_round_losers():
    module = demo_module()
    config = module.synthetic_config()
    EventConfig.model_validate(config)
    assert publication_content_issues(config) == []
    assert [c["label"] for c in config["courts"]] == list("ABCD")
    division = config["divisions"][0]
    placements = division["format_plan"]["placement_matches"]
    assert placements == ["bronze"]
    assert division["name"] == "國中男子組"
    assert config["draw_defaults"]["seeded_entries_mode"] == "none"
    assert "歷史原稿敗者來源" in module.NOTICE
    assert not config["historical_review"]["original_attachments_revalidated"]


def test_output_directory_must_be_fresh(tmp_path):
    import pytest

    module = demo_module()
    existing_font = tmp_path / "local.ttf"
    existing_font.write_bytes(b"only path existence is needed before output rejection")
    with pytest.raises(FileExistsError):
        module.build(tmp_path, existing_font, documents=False)
