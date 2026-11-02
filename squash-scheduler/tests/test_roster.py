from __future__ import annotations

import subprocess
import zipfile
from copy import deepcopy

import pytest
from docx import Document
from docx.oxml import OxmlElement

from squash_scheduler.domain.models import (
    Division,
    Entry,
    Participant,
    Registration,
    Snapshot,
    new_id,
)
from squash_scheduler.domain.registrations import division_check, resolve_roster, transfer_entry
from squash_scheduler.domain.validation import validate_references
from squash_scheduler.importers import legacy_doc
from squash_scheduler.importers.legacy_doc import RosterError
from squash_scheduler.importers.word import import_roster


@pytest.fixture
def snapshot():
    divisions = [
        Division(
            name=f"男子 {level}", level_order=i, gender_category="male", category="competitive"
        )
        for i, level in enumerate("BCD")
    ]
    return Snapshot(
        config={
            "registration": {"division_aliases": {"男B": divisions[0].id}},
            "merge_whitelist": [
                {"source_division_id": divisions[0].id, "target_division_id": divisions[1].id}
            ],
        },
        divisions=divisions,
    )


def test_identity_resolution_cannot_bypass_reviewed_transfer_for_a_known_source_division(
    snapshot, tmp_path
):
    path = roster(tmp_path, ["男B", "匿名選手"])
    report = import_roster(snapshot, path)
    candidate = players(report)[0]
    before = snapshot.model_dump(mode="json")
    with pytest.raises(RosterError, match="approved transfer"):
        resolve_roster(
            snapshot,
            {
                "batch_id": report["batch_id"],
                "resolutions": [
                    {
                        "candidate_id": candidate["id"],
                        "division_id": snapshot.divisions[1].id,
                        "create_participant": {
                            "official_name": "匿名選手",
                            "display_name": "匿名選手",
                        },
                    }
                ],
            },
        )
    assert snapshot.model_dump(mode="json") == before


def roster(tmp_path, paragraphs):
    document = Document()
    for value in paragraphs:
        document.add_paragraph(value)
    path = tmp_path / "roster.docx"
    document.save(path)
    return path


def players(report):
    return [c for c in report["candidates"] if c["status"] != "ignored_with_reason"]


def enroll(snapshot, division, participant=None):
    participant = participant or Participant(official_name="Player", display_name="Player")
    if not any(p.id == participant.id for p in snapshot.participants):
        snapshot.participants.append(participant)
    registration = Registration(
        participant_id=participant.id,
        original_division_id=division.id,
        source_ref={"raw_text": participant.official_name},
    )
    entry = Entry(registration_id=registration.id, effective_division_id=division.id)
    snapshot.registrations.append(registration)
    snapshot.entries.append(entry)
    return entry


def approved(entry, target, **extra):
    return {
        "source_entry_id": entry.id,
        "target_division_id": target.id,
        "status": "approved",
        "eligibility_review": {"status": "approved", "basis": "Organizer checked eligibility"},
        **extra,
    }


def test_import_is_staged_and_idempotent_after_identity_resolution(snapshot, tmp_path):
    path = roster(tmp_path, ["男B", "1. 王小明", "2. 李小華（俱樂部）"])
    report = import_roster(snapshot, path)
    assert len(snapshot.entries) == 0
    assert report["counts"]["needs_resolution"] == 2
    report = resolve_roster(
        snapshot,
        {
            "batch_id": report["batch_id"],
            "resolutions": [
                {
                    "candidate_id": c["id"],
                    "create_participant": {"official_name": c["official_name"]},
                }
                for c in players(report)
            ],
        },
    )
    assert report["status"] == "applied"
    assert report["counts"]["accepted_registrations"] == 2
    assert import_roster(snapshot, path)["idempotent"]
    assert len(snapshot.entries) == 2
    assert len(snapshot.sources) == len(snapshot.imports) == 1
    assert snapshot.registrations[1].source_ref["raw_text"] == "2. 李小華（俱樂部）"
    assert validate_references(snapshot) == []
    assert not [i for i in snapshot.issues if i.status == "open"]


def test_same_names_remain_distinct_until_explicit_cross_division_mapping(snapshot, tmp_path):
    report = import_roster(
        snapshot, roster(tmp_path, ["男子 B", "陳同名", "陳同名", "男子 C", "陳同名"])
    )
    first, second, third = players(report)
    resolve_roster(
        snapshot,
        {
            "batch_id": report["batch_id"],
            "resolutions": [
                {"candidate_id": first["id"], "create_participant": {"official_name": "陳同名"}},
                {"candidate_id": second["id"], "create_participant": {"official_name": "陳同名"}},
            ],
        },
    )
    assert len(snapshot.participants) == 2
    report = resolve_roster(
        snapshot,
        {
            "batch_id": report["batch_id"],
            "resolutions": [
                {"candidate_id": third["id"], "participant_id": snapshot.participants[0].id}
            ],
        },
    )
    assert len(snapshot.participants) == 2
    assert len(report["dual_registrations"]) == 1
    assert snapshot.registrations[2].participant_id == snapshot.participants[0].id


def test_changed_source_reports_missing_person_without_withdrawing(snapshot, tmp_path):
    report = import_roster(snapshot, roster(tmp_path, ["男B", "王小明", "李小華"]))
    resolve_roster(
        snapshot,
        {
            "batch_id": report["batch_id"],
            "resolutions": [
                {
                    "candidate_id": c["id"],
                    "create_participant": {"official_name": c["official_name"]},
                }
                for c in players(report)
            ],
        },
    )
    prior_registration = snapshot.registrations[1].id
    report = import_roster(snapshot, roster(tmp_path, ["男B", "王小明"]))
    assert report["removal_candidates"][0]["registration_id"] == prior_registration
    assert len(snapshot.entries) == 2 and all(e.status == "active" for e in snapshot.entries)
    assert any(i["code"] == "REGISTRATION_REMOVAL_REVIEW_REQUIRED" for i in report["issues"])


def test_resolution_patch_is_atomic_and_duplicate_division_is_rejected(snapshot, tmp_path):
    report = import_roster(snapshot, roster(tmp_path, ["男B", "甲", "乙"]))
    first, second = players(report)
    pid = new_id()
    before = snapshot.model_dump()
    with pytest.raises(RosterError, match="already has an active entry"):
        resolve_roster(
            snapshot,
            {
                "batch_id": report["batch_id"],
                "resolutions": [
                    {
                        "candidate_id": first["id"],
                        "create_participant": {"id": pid, "official_name": "甲"},
                    },
                    {"candidate_id": second["id"], "participant_id": pid},
                ],
            },
        )
    assert snapshot.model_dump() == before


def test_late_roster_resolution_invalidates_only_affected_plans_and_draws(snapshot, tmp_path):
    affected, other, _ = snapshot.divisions
    affected.format_plan = {"status": "approved", "approval_id": new_id()}
    other.format_plan = {"status": "approved", "approval_id": new_id()}
    affected_draw, other_draw = new_id(), new_id()
    snapshot.draws = [
        {"id": affected_draw, "division_id": affected.id, "status": "approved"},
        {"id": other_draw, "division_id": other.id, "status": "approved"},
    ]
    snapshot.approvals = [
        {"target_id": affected.id, "target_type": "format"},
        {"target_id": affected_draw, "target_type": "draw"},
        {"target_id": snapshot.id, "target_type": "revision"},
        {"target_id": other.id, "target_type": "format"},
    ]
    report = import_roster(snapshot, roster(tmp_path, ["男B", "王小明"]))
    resolve_roster(
        snapshot,
        {
            "batch_id": report["batch_id"],
            "resolutions": [
                {
                    "candidate_id": players(report)[0]["id"],
                    "create_participant": {"official_name": "王小明"},
                }
            ],
        },
    )
    assert snapshot.divisions[0].format_plan["status"] == "review_required"
    assert "approval_id" not in snapshot.divisions[0].format_plan
    assert snapshot.divisions[1].format_plan["status"] == "approved"
    assert snapshot.draws[0]["status"] == "superseded"
    assert snapshot.draws[1]["status"] == "approved"
    assert [a.get("status") for a in snapshot.approvals] == ["stale", "stale", "stale", None]
    assert snapshot.status == "review_required"


def test_word_body_order_runs_table_headers_and_provenance(snapshot, tmp_path):
    document = Document()
    document.add_paragraph("男子 B")
    p = document.add_paragraph()
    p.add_run("1. 王")
    p.add_run("小明")
    table = document.add_table(rows=3, cols=2)
    for row, values in zip(
        table.rows, [("姓名", "學校"), ("林同學", "甲校"), ("姓名", "學校")], strict=True
    ):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    document.add_paragraph("男子 C")
    document.add_paragraph("張同學\n李同學")
    path = tmp_path / "ordered.docx"
    document.save(path)
    report = import_roster(snapshot, path)
    candidates = players(report)
    assert [c["official_name"] for c in candidates] == ["王小明", "林同學", "張同學", "李同學"]
    assert candidates[0]["source_ref"]["paragraph_index"] == 1
    assert candidates[1]["source_ref"]["table_index"] == 0
    assert candidates[1]["source_ref"]["row_index"] == 1
    assert candidates[1]["source_ref"]["cell_index"] == 0
    assert candidates[2]["division_id"] == snapshot.divisions[1].id
    assert candidates[3]["source_ref"]["line_index"] == 1
    assert all(c["source_ref"]["page"] is None for c in candidates)
    assert report["ignored_reasons"]["table_header"] == 4


def test_unknown_heading_and_sponsors_never_become_official_players(snapshot, tmp_path):
    report = import_roster(snapshot, roster(tmp_path, ["贊助：某公司", "未知組", "王小明"]))
    assert report["candidates"][0]["ignored_reason"] == "non_roster_text"
    assert all(c["status"] == "needs_review" for c in players(report))
    assert not snapshot.entries


def test_merged_title_supported_but_nested_table_blocks_resolution(snapshot, tmp_path):
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "男子 B"
    table.cell(1, 0).text = "1. 王小明"
    path = tmp_path / "merged.docx"
    document.save(path)
    report = import_roster(snapshot, path)
    assert report["status"] == "staged"
    assert players(report)[0]["official_name"] == "王小明"
    table.cell(1, 1).add_table(rows=1, cols=1).cell(0, 0).text = "Nested name"
    document.save(path)
    report = import_roster(snapshot, path)
    assert report["status"] == "blocked"
    assert any(i["code"] == "IMPORT_UNSUPPORTED_STRUCTURE" for i in report["issues"])
    with pytest.raises(RosterError):
        resolve_roster(snapshot, {"batch_id": report["batch_id"], "resolutions": []})


def test_doc_without_converter_is_explicitly_blocked(snapshot, tmp_path, monkeypatch):
    path = tmp_path / "input.doc"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"synthetic")
    monkeypatch.setattr(legacy_doc.shutil, "which", lambda _: None)
    report = import_roster(snapshot, path)
    assert report["status"] == "blocked"
    assert report["issues"][0]["code"] == "IMPORT_CONVERTER_UNAVAILABLE"
    assert not snapshot.entries


@pytest.mark.parametrize("name", ["../escape.xml", "/absolute.xml", "C:/bad.xml"])
def test_unsafe_zip_paths_are_blocked(snapshot, tmp_path, name):
    path = tmp_path / "malicious.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "evil")
    report = import_roster(snapshot, path)
    assert report["issues"][0]["code"] == "IMPORT_UNSAFE_INPUT"


def test_external_relationship_and_entity_xml_are_blocked(snapshot, tmp_path):
    path = tmp_path / "external.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "_rels/.rels",
            '<Relationships><Relationship TargetMode="External" Target="https://example.invalid"/></Relationships>',
        )
    assert import_roster(snapshot, path)["issues"][0]["code"] == "IMPORT_UNSUPPORTED_STRUCTURE"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("_rels/.rels", '<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]><a/>')
    assert import_roster(snapshot, path)["issues"][0]["code"] == "IMPORT_UNSAFE_INPUT"


def test_picture_only_roster_blocks(snapshot, tmp_path):
    document = Document()
    document.add_paragraph()._p.append(OxmlElement("w:drawing"))
    path = tmp_path / "scan.docx"
    document.save(path)
    assert import_roster(snapshot, path)["status"] == "blocked"


def test_size_limit_uses_actual_file_size(snapshot, tmp_path):
    path = roster(tmp_path, ["男B", "甲"])
    snapshot.config["runtime"] = {"import_max_file_mib": 0.001}
    with pytest.raises(RosterError) as caught:
        import_roster(snapshot, path)
    assert caught.value.code == "IMPORT_UNSAFE_INPUT"


def test_converter_is_isolated_macro_disabled_and_bounded(tmp_path, monkeypatch):
    source = tmp_path / "source.doc"
    source.write_bytes(b"synthetic")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(legacy_doc.shutil, "which", lambda _: "soffice")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        (output / "source.docx").write_bytes(b"converted")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(legacy_doc.subprocess, "run", run)
    assert legacy_doc.convert_legacy(source, output, 7).is_file()
    command, kwargs = calls[0]
    assert kwargs["shell"] is False and kwargs["timeout"] == 7
    assert any(arg.startswith("-env:UserInstallation=file:") for arg in command)
    profile = (output / "profile/user/registrymodifications.xcu").read_text()
    assert "DisableMacrosExecution" in profile and "MacroSecurityLevel" in profile
    assert 'oor:name="Link"' in profile


def test_converter_timeout_is_explicit(tmp_path, monkeypatch):
    source = tmp_path / "source.doc"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(legacy_doc.shutil, "which", lambda _: "soffice")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(legacy_doc.subprocess, "run", timeout)
    with pytest.raises(RosterError) as caught:
        legacy_doc.convert_legacy(source, tmp_path / "converted", 1)
    assert caught.value.code == "IMPORT_CONVERSION_FAILED"


def test_counts_single_two_empty_and_custom_minimum_are_explicit(snapshot):
    enroll(snapshot, snapshot.divisions[0])
    for _ in range(2):
        enroll(snapshot, snapshot.divisions[1])
    before = snapshot.model_dump()
    report = division_check(snapshot)
    assert {i["code"]: i["severity"] for i in report["issues"]} == {
        "DIVISION_SINGLE_ENTRY": "error",
        "DIVISION_TWO_ENTRIES": "warning",
        "DIVISION_EMPTY": "info",
    }
    assert report["counts"][snapshot.divisions[2].id] == 0
    assert snapshot.model_dump() == before
    snapshot.divisions[1].minimum_entries = 5
    enroll(snapshot, snapshot.divisions[1])
    assert any(i["code"] == "DIVISION_BELOW_MINIMUM" for i in division_check(snapshot)["issues"])


def test_suggestions_are_directed_adjacent_same_gender_competitive_only(snapshot):
    source, target, distant = snapshot.divisions
    enroll(snapshot, source)
    enroll(snapshot, target)
    snapshot.config["merge_whitelist"] += [
        {"source_division_id": source.id, "target_division_id": distant.id}
    ]
    assert [
        (s["source_division_id"], s["target_division_id"])
        for s in division_check(snapshot)["suggestions"]
    ] == [(source.id, target.id)]
    target.gender_category = "female"
    assert division_check(snapshot)["suggestions"] == []
    target.gender_category = "male"
    target.category = "school"
    assert division_check(snapshot)["suggestions"] == []


def test_transfer_preserves_original_and_dual_registration_and_invalidates(snapshot):
    source, target, other = snapshot.divisions
    entry = enroll(snapshot, source)
    participant = snapshot.participants[0]
    dual = enroll(snapshot, other, participant)
    original = snapshot.registrations[0].model_dump()
    source.format_plan = {"status": "approved", "approval_id": new_id()}
    snapshot.approvals = [
        {"id": new_id(), "target_id": source.id, "status": "approved"},
        {"id": new_id(), "target_id": other.id, "status": "approved"},
    ]
    report = transfer_entry(
        snapshot, approved(entry, target), "organizer", "Eligible neighboring level"
    )
    assert snapshot.registrations[0].model_dump() == original
    assert entry.status == "transferred"
    assert dual.status == "active" and dual.effective_division_id == other.id
    assert snapshot.entries[-1].registration_id == entry.registration_id
    assert report["counts"][source.id] == 0 and report["counts"][target.id] == 1
    assert snapshot.approvals[0]["status"] == "stale"
    assert snapshot.approvals[1]["status"] == "approved"
    assert source.format_plan["status"] == "review_required"
    assert report["transfer"]["target_entry_id"] == snapshot.entries[-1].id


def test_unapproved_transfer_and_duplicate_target_do_not_mutate(snapshot):
    source, target, _ = snapshot.divisions
    entry = enroll(snapshot, source)
    before = deepcopy(snapshot.model_dump())
    with pytest.raises(RosterError) as caught:
        transfer_entry(
            snapshot,
            {"source_entry_id": entry.id, "target_division_id": target.id},
            "actor",
            "reason",
        )
    assert caught.value.code == "MERGE_APPROVAL_REQUIRED"
    assert snapshot.model_dump() == before
    enroll(snapshot, target, snapshot.participants[0])
    before = deepcopy(snapshot.model_dump())
    with pytest.raises(RosterError) as caught:
        transfer_entry(snapshot, approved(entry, target), "actor", "reason")
    assert caught.value.code == "DUPLICATE_EFFECTIVE_REGISTRATION"
    assert snapshot.model_dump() == before


def test_special_transfer_requires_review_and_cycle_is_rejected(snapshot):
    source, _target, distant = snapshot.divisions
    entry = enroll(snapshot, source)
    with pytest.raises(RosterError) as caught:
        transfer_entry(snapshot, approved(entry, distant), "actor", "reason")
    assert caught.value.code == "TRANSFER_SPECIAL_REQUEST_REQUIRED"
    transfer_entry(
        snapshot,
        approved(entry, distant, special_request=True),
        "actor",
        "Checked special eligibility",
    )
    with pytest.raises(RosterError) as caught:
        transfer_entry(
            snapshot,
            approved(snapshot.entries[-1], source, special_request=True),
            "actor",
            "return",
        )
    assert caught.value.code == "TRANSFER_CYCLE"
