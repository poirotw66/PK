"""The local setup UI's preview/confirm flow reaches the real scheduler."""

import io

import pytest
from openpyxl import Workbook

from squash_scheduler.domain.validation import validate
from squash_scheduler.local_workflow import LocalWorkflow, event_config
from squash_scheduler.operations import OperatorService
from squash_scheduler.storage.repository import Repository


def settings():
    return {
        "name": "Synthetic local workflow",
        "venue": "Synthetic courts",
        "dates": ["2030-01-01"],
        "start": "08:00",
        "end": "17:30",
        "courts": ["Synthetic A", "Synthetic B"],
        "grid": 15,
        "play": 20,
        "buffer": 10,
        "rest": 60,
        "daily_limit": 5,
        "transfer": 0,
        "ranking_delay": 0,
        "participant_scope": "confirmed_only",
        "best_of": 3,
        "target": 11,
        "division_classifications": {
            "Synthetic Men A": {"category": "competitive", "gender_category": "male"},
            "Synthetic Men B": {"category": "competitive", "gender_category": "male"},
        },
    }


def roster():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["姓名", "組別"])
    for group in ("A", "B"):
        for index in range(1, 4):
            sheet.append([f"Synthetic {group}{index}", f"Synthetic Men {group}"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def workflow(tmp_path):
    service = OperatorService(Repository(tmp_path / "new-tournament"))
    yield LocalWorkflow(service)
    service.close()


def request(workflow):
    uploaded = workflow.upload("synthetic-roster.xlsx", roster())
    return {
        "actor": "synthetic-operator",
        "reason": "Reviewed synthetic roster and event settings",
        "uploads": [uploaded["id"]],
        "settings": settings(),
        "rows": [
            {**row, "identity": row["name"], "source_id": uploaded["id"]}
            for row in uploaded["rows"]
        ],
    }


def setup(workflow, payload=None):
    plan = workflow.preview(payload or request(workflow))
    result = workflow.confirm({"id": plan["id"], "hash": plan["hash"]})
    return workflow.service.repo.load(), result


def test_excel_upload_and_preview_do_not_write_tournament(workflow):
    assert not workflow.state()["initialized"]
    payload = request(workflow)
    plan = workflow.preview(payload)
    assert plan["summary"]["entries"] == 6
    assert plan["summary"]["participants"] == 6
    assert len(plan["summary"]["divisions"]) == 2
    assert not workflow.initialized()
    assert not (workflow.service.repo.root / "source").exists()
    assert "raw" not in workflow.state()["uploads"][0]


def test_confirm_archives_original_and_reviewed_source_and_passes_identity_gate(workflow):
    payload = request(workflow)
    raw = workflow.uploads[payload["uploads"][0]]["raw"]
    plan = workflow.preview(payload)
    workflow.confirm({"id": plan["id"], "hash": plan["hash"]})
    saved = workflow.service.repo.load()
    assert len(saved.entries) == len(saved.registrations) == len(saved.participants) == 6
    assert validate(saved, "G1")["passed"]
    assert saved.imports[0]["status"] == "applied"
    assert len(saved.sources) == 2
    archived = {
        source["original_name"]: (
            workflow.service.repo.root / "source" / source["id"] / "original"
        ).read_bytes()
        for source in saved.sources
    }
    assert archived["synthetic-roster.xlsx"] == raw
    assert b"Synthetic A1" in archived["reviewed-rows.json"]
    assert saved.changes[-1]["actor_id"] == "synthetic-operator"
    assert workflow.state()["uploads"] == []


def test_review_is_single_use_and_never_overwrites_existing_event(workflow):
    payload = request(workflow)
    plan = workflow.preview(payload)
    confirmation = {"id": plan["id"], "hash": plan["hash"]}
    workflow.confirm(confirmation)
    revision = workflow.service.repo.load().id
    with pytest.raises(ValueError):
        workflow.confirm(confirmation)
    with pytest.raises(ValueError, match="已有賽事"):
        workflow.preview(payload)
    assert workflow.service.repo.load().id == revision


def test_new_preview_invalidates_previous_review(workflow):
    payload = request(workflow)
    old = workflow.preview(payload)
    payload["settings"]["name"] = "Reviewed replacement event name"
    current = workflow.preview(payload)
    with pytest.raises(ValueError, match="失效"):
        workflow.confirm({"id": old["id"], "hash": old["hash"]})
    assert not workflow.initialized()
    workflow.confirm({"id": current["id"], "hash": current["hash"]})
    assert workflow.service.repo.load().config["event"]["name"] == payload["settings"]["name"]


def test_modified_confirmation_hash_cannot_create_event(workflow):
    plan = workflow.preview(request(workflow))
    with pytest.raises(ValueError, match="失效"):
        workflow.confirm({"id": plan["id"], "hash": "wrong"})
    assert not workflow.initialized()


def test_same_identity_can_enter_two_divisions_but_not_twice_in_one(workflow):
    payload = request(workflow)
    payload["rows"][3]["identity"] = payload["rows"][0]["identity"]
    payload["rows"][3]["name"] = payload["rows"][0]["name"]
    saved, _ = setup(workflow, payload)
    assert len(saved.entries) == 6
    assert len(saved.participants) == 5
    registrations = {reg.id: reg for reg in saved.registrations}
    assert (
        registrations[saved.entries[0].registration_id].participant_id
        == registrations[saved.entries[3].registration_id].participant_id
    )


def test_duplicate_registration_requires_review_and_does_not_save(workflow):
    payload = request(workflow)
    payload["rows"][1]["identity"] = payload["rows"][0]["identity"]
    with pytest.raises(ValueError, match="already has an active entry"):
        workflow.preview(payload)
    assert not workflow.initialized()


def test_unknown_source_id_and_expired_upload_are_rejected(workflow):
    payload = request(workflow)
    payload["rows"][0]["source_id"] = "unknown-source"
    with pytest.raises(ValueError, match="來源"):
        workflow.preview(payload)
    payload["rows"][0]["source_id"] = payload["uploads"][0]
    payload["uploads"] = ["unknown-source"]
    with pytest.raises(ValueError, match="失效"):
        workflow.preview(payload)


def test_manually_reviewed_rows_are_archived_without_an_uploaded_file(workflow):
    payload = request(workflow)
    payload["uploads"] = []
    for row in payload["rows"]:
        row.pop("source_id")
    saved, _ = setup(workflow, payload)
    assert len(saved.sources) == 1
    assert saved.sources[0]["original_name"] == "reviewed-rows.json"
    assert validate(saved, "G1")["passed"]


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"dates": []},
        {"dates": ["2030-01-01", "2030-01-01"]},
        {"end": "07:00"},
        {"courts": []},
        {"courts": ["A", " A "]},
        {"grid": True},
        {"rest": -1},
        {"participant_scope": "guess"},
        {"best_of": 2},
        {"target": 10},
        {"walkover_rule": ["unknown"]},
        {"division_classifications": {}},
    ],
)
def test_invalid_event_parameters_do_not_create_event(workflow, change):
    payload = request(workflow)
    payload["settings"].update(change)
    with pytest.raises(ValueError):
        workflow.preview(payload)
    assert not workflow.initialized()


def test_empty_settings_and_rules_remain_explicit(workflow):
    with pytest.raises(ValueError):
        event_config({}, ["Synthetic Men A"], "synthetic-operator")
    snapshot, _ = setup(workflow)
    assert snapshot.config["match_rule_defaults"]["walkover_rule"] is None
    assert snapshot.config["match_rule_defaults"]["best_of_games"] == 3


def test_intake_reaches_real_format_draw_and_schedule_flow(workflow):
    initial, _ = setup(workflow)
    service = workflow.service

    def apply(action, **values):
        preview = service.preview(
            {
                "action": action,
                "revision_id": service.repo.load().id,
                "actor": "synthetic-operator",
                "reason": "Reviewed synthetic end-to-end operation",
                **values,
            }
        )
        return service.confirm({"id": preview["id"], "hash": preview["hash"]})

    for index, division in enumerate(initial.divisions):
        apply(
            "format",
            division_id=division.id,
            format={
                "template_id": "round_robin" if index == 0 else "single_elimination",
                "pools": [],
                "bronze": False,
                "encouragement": False,
            },
        )
        if index == 1:
            apply("draw", division_id=division.id, seed=42)
            draw = service.repo.load().draws[-1]
            apply("approve_draw", draw_id=draw["id"])
    assert len(service.repo.load().matches) == 5
    apply("schedule")
    scheduled = service.repo.load()
    assert len(scheduled.assignments) == 5
    assert {match.display_code for match in scheduled.matches} == {"1", "2", "3", "4", "5"}
    from squash_scheduler.domain.match_numbers import chronological_match_numbers

    assert {
        match.id: match.display_code for match in scheduled.matches
    } == chronological_match_numbers(scheduled)
    report = validate(scheduled, "G4")
    assert report["passed"], report["issues"]
    assert len(list((service.repo.root / "revisions").iterdir())) >= 6
    operation = {
        "action": "schedule",
        "actor": "synthetic-operator",
        "reason": "Keep issued match codes",
        "assignments": [a.model_dump(mode="json") for a in scheduled.assignments],
    }
    repeated, _ = service._execute(scheduled, operation)
    assert [m.display_code for m in repeated.matches] == [m.display_code for m in scheduled.matches]
    for match in scheduled.matches:
        match.display_code = None
    scheduled.changes.append({"kind": "cloud_return", "live": {"starts": {}, "actual_courts": {}}})
    returned, _ = service._execute(scheduled, operation)
    assert all(match.display_code is None for match in returned.matches)


def test_repeated_source_upload_returns_one_staged_source(workflow):
    raw = roster()
    first = workflow.upload("synthetic.xlsx", raw)
    second = workflow.upload("synthetic.xlsx", raw)
    assert first == second
    assert len(workflow.state()["uploads"]) == 1


def test_review_normalizes_surrounding_spaces_without_mutating_request(workflow):
    payload = request(workflow)
    payload["rows"][0]["name"] = "  Synthetic A1  "
    payload["rows"][0]["division"] = "  Synthetic Men A  "
    saved, _ = setup(workflow, payload)
    assert saved.participants[0].official_name == "Synthetic A1"
    assert len(saved.divisions) == 2
    assert payload["rows"][0]["name"] == "  Synthetic A1  "


def test_cloud_compatibility_report_is_available_before_scheduling(workflow):
    payload = request(workflow)
    payload["settings"].update(rest=45, daily_limit=4, participant_scope="all_possible")
    preview = workflow.preview(payload)
    report = preview["summary"]["cloud_compatibility"]
    assert report["compatible"] is False
    assert {item["key"] for item in report["checks"] if item["status"] == "block"} == {
        "rest",
        "daily_limit",
        "participant_scope",
    }
    assert not workflow.initialized()
    workflow.confirm({"id": preview["id"], "hash": preview["hash"]})
    assert workflow.state()["cloud_compatibility"]["compatible"] is False


def test_cloud_wider_court_hours_warn_without_rejecting_valid_local_settings(workflow):
    payload = request(workflow)
    payload["settings"].update(start="07:00", end="19:00")
    report = workflow.preview(payload)["summary"]["cloud_compatibility"]
    assert report["compatible"] is True
    assert (
        next(item for item in report["checks"] if item["key"] == "start_window")["status"]
        == "warning"
    )


def test_same_name_review_reports_distinct_people_and_never_auto_merges(workflow):
    payload = request(workflow)
    payload["rows"][3]["name"] = payload["rows"][0]["name"]
    preview = workflow.preview(payload)
    duplicate = preview["summary"]["identity_review"][0]
    assert duplicate["rows"] == [1, 4]
    assert duplicate["identities"] == 2
    assert preview["summary"]["participants"] == 6


def test_explicit_special_policies_are_preserved_in_initial_setup(workflow):
    payload = request(workflow)
    policy = {
        "include_in_rankings": True,
        "counts_as_played": False,
        "winner_points": 2,
        "loser_points": 0,
    }
    payload["settings"].update(walkover_rule=policy, ranking_denominator_rule={"walkover": True})
    saved, _ = setup(workflow, payload)
    assert saved.config["match_rule_defaults"]["walkover_rule"] == policy
    assert saved.config["match_rule_defaults"]["ranking_denominator_rule"] == {"walkover": True}
