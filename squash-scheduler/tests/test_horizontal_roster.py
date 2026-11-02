"""Explicit source-bound horizontal layouts retain ambiguous spacing and review history."""

import hashlib

import pytest
from docx import Document
from test_roster import snapshot as roster_snapshot

from squash_scheduler.domain.config import ParserProfile
from squash_scheduler.exporters.documents import render_html, view_model
from squash_scheduler.importers import word
from squash_scheduler.importers.legacy_doc import RosterError

snapshot = roster_snapshot


def horizontal_source(tmp_path):
    document = Document()
    document.add_paragraph("匿名賽事名單")
    document.add_paragraph("男B")
    line = "甲一  甲二  王  三  Alex Lee\t林四"
    document.add_paragraph(line)
    path = tmp_path / "horizontal.docx"
    document.save(path)
    return path, line


def profile(path):
    return {
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "layout": "horizontal_whitespace",
        "join_single_cjk_pairs": True,
        "ignored_paragraphs": [0],
    }


def test_source_bound_profile_preserves_full_names_and_character_locations(snapshot, tmp_path):
    path, line = horizontal_source(tmp_path)
    snapshot.config["registration"]["parser_profiles"] = [profile(path)]
    report = word.import_roster(snapshot, path)
    candidates = [c for c in report["candidates"] if c["status"] != "ignored_with_reason"]
    assert [c["official_name"] for c in candidates] == [
        "甲一",
        "甲二",
        "王  三",
        "Alex Lee",
        "林四",
    ]
    assert candidates[2]["status"] == "needs_review"
    for candidate in candidates:
        source_ref = candidate["source_ref"]
        assert source_ref["raw_line"] == line
        assert (
            line[source_ref["character_start"] : source_ref["character_end"]]
            == candidate["official_name"]
        )
        assert source_ref["page"] is None
    assert not snapshot.entries and not snapshot.participants
    again = word.import_roster(snapshot, path)
    assert again["idempotent"] and again["batch_id"] == report["batch_id"]


def test_profile_change_supersedes_only_unapplied_staging_and_reparse_is_traceable(
    snapshot, tmp_path
):
    path, _ = horizontal_source(tmp_path)
    before = word.import_roster(snapshot, path)
    snapshot.config["registration"]["parser_profiles"] = [profile(path)]
    updated = word.import_roster(snapshot, path)
    assert updated["batch_id"] != before["batch_id"]
    assert snapshot.imports[0]["status"] == "superseded"
    assert snapshot.imports[0]["superseded_by_batch_id"] == updated["batch_id"]
    assert snapshot.imports[0]["parser_config_hash"] != snapshot.imports[1]["parser_config_hash"]
    assert all(
        i.status == "resolved"
        for i in snapshot.issues
        if i.entity_id in {c["id"] for c in before["candidates"]}
    )
    assert not snapshot.entries


def test_profile_does_not_apply_to_another_file(snapshot, tmp_path):
    path, line = horizontal_source(tmp_path)
    settings = {**profile(path), "source_sha256": "0" * 64}
    snapshot.config["registration"]["parser_profiles"] = [settings]
    report = word.import_roster(snapshot, path)
    assert any(c["official_name"] == line for c in report["candidates"])


def test_blocked_conversion_can_retry_after_tool_becomes_available(snapshot, tmp_path, monkeypatch):
    source = tmp_path / "legacy.doc"
    source.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"synthetic")

    def missing(*_):
        raise RosterError("IMPORT_CONVERTER_UNAVAILABLE", "Synthetic unavailable converter")

    monkeypatch.setattr(word, "convert_legacy", missing)
    failed = word.import_roster(snapshot, source)
    assert failed["status"] == "blocked"

    def installed(_source, output, _timeout):
        doc = Document()
        doc.add_paragraph("男B")
        doc.add_paragraph("匿名選手")
        path = output / "converted.docx"
        doc.save(path)
        return path

    monkeypatch.setattr(word, "convert_legacy", installed)
    retried = word.import_roster(snapshot, source)
    assert retried["status"] == "staged" and retried["batch_id"] != failed["batch_id"]
    assert snapshot.imports[0]["status"] == "superseded"
    assert not any(
        i.status == "open" and i.code == "IMPORT_CONVERTER_UNAVAILABLE" for i in snapshot.issues
    )


def test_ignored_paragraph_indices_reject_boolean_values():
    with pytest.raises(ValueError):
        ParserProfile(source_sha256="0" * 64, ignored_paragraphs=[True])


def test_unresolved_source_names_are_visible_in_draft_review_but_not_formal_documents(
    snapshot, tmp_path
):
    path, _ = horizontal_source(tmp_path)
    snapshot.config["registration"]["parser_profiles"] = [profile(path)]
    word.import_roster(snapshot, path)
    draft = view_model(snapshot)
    assert len(draft["roster_candidates"]) == 5
    html = render_html(draft, tmp_path)
    assert "名單暫存審核" in html and "Alex Lee" in html and "王  三" in html
    assert "字元" in html and "段落" in html
    assert view_model(snapshot, draft=False)["roster_candidates"] == []
