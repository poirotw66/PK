"""Small evidence fixtures exercise differences, without importing tournament code."""

import importlib.util
from pathlib import Path

import pymupdf
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples/compare_booklet_baseline.py"
SPEC = importlib.util.spec_from_file_location("booklet_baseline", SCRIPT)
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


def test_counts_preserve_unnumbered_and_unknown_evidence():
    old = [
        {"code": "1", "stage": "", "time": "09:00", "raw_court": None},
        {"code": "review-1", "stage": "exhibition", "time": None, "raw_court": None},
    ]
    new = [{"code": "DEMO-1", "stage": "round robin", "court": "A"}]
    result = baseline.compare_counts(old, new)
    assert result["delta"] == -1
    assert result["original_numbered_rows"] == 1
    assert result["original_unnumbered_rows"] == 1
    assert result["original_unknown_courts"] == 2
    assert result["original_unknown_times"] == 1
    assert old[1]["time"] is None  # a new schedule must not backfill historical facts


def test_group_alias_does_not_normalize_participant_display():
    assert baseline.group_label("國中男") == "國中男子組"
    assert baseline.group_label("SyntheticAlias007") == "SyntheticAlias007"
    assert baseline.group_label("SyntheticPlayer023") == "SyntheticPlayer023"


def test_source_verification_rejects_sha_and_clip_drift(tmp_path):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "1 Alice Bob")
        doc.save(source)
    digest = baseline.sha(source)
    original = {
        "matches": [
            {
                "code": "1",
                "side_a": "Alice",
                "side_b": "Bob",
                "source_ref": {
                    "sha256": digest,
                    "page": 1,
                    "bbox": [0, 0, 300, 150],
                    "text": "1 Alice Bob",
                },
            }
        ]
    }
    summary = {"sha256": digest, "page_count": 1}
    assert (
        baseline.verify_evidence(source, original, {}, summary)["verified_schedule_row_clips"] == 1
    )
    original["matches"][0]["source_ref"]["text"] = "1 Alice Eve"
    with pytest.raises(ValueError, match="Source row text differs"):
        baseline.verify_evidence(source, original, {}, summary)
    summary["sha256"] = "invalid"
    with pytest.raises(ValueError, match="SHA-256"):
        baseline.verify_evidence(source, original, {}, summary)
