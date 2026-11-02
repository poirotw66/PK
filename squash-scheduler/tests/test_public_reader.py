"""Run the credential-free data-source contract checks with Node's built-in runner."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is required for browser-module checks"
)
def test_public_reader_data_source_contract():
    project = Path(__file__).parents[1]
    completed = subprocess.run(
        [
            "node",
            "--test",
            "tests/test_live_source.mjs",
            "tests/test_live_polygon.mjs",
            "tests/test_draft_storage.mjs",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
