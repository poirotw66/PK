"""Public booklet integration must preserve effective rules and publication boundaries."""

from __future__ import annotations

import pytest
from test_documents import document_event

from squash_scheduler.domain.validation import validate
from squash_scheduler.exporters.documents import render_html, view_model


def test_incomplete_prospectus_is_visible_as_pending_in_draft_and_blocks_formal(tmp_path):
    snapshot = document_event()
    snapshot.config["publication"].update(
        layout_template_id="reference-v2",
        document_profile="prospectus",
        prospectus={"approval_status": "draft", "organizers": "主辦單位"},
    )
    draft = view_model(snapshot)
    html = render_html(draft, tmp_path)
    assert "競賽簡章暨秩序冊" in html and "主辦單位" in html
    assert "待補（須由主辦單位確認）" in html
    assert "Content " not in html and "Revision " not in html
    paths = {issue.get("field_path") for issue in validate(snapshot, "G5")["issues"]}
    assert "publication.prospectus.registration" in paths
    assert "publication.prospectus.approval_status" in paths
    with pytest.raises(ValueError, match="Formal prospectus content is incomplete"):
        render_html(view_model(snapshot, draft=False), tmp_path)


def test_demo_marker_survives_public_translation_and_blocks_formal_publication(tmp_path):
    snapshot = document_event()
    for match in snapshot.matches:
        match.rule_snapshot["match_rules"]["late_arrival_rule"] = {
            "grace_minutes": 5,
            "demo_only": True,
        }
    model = view_model(snapshot)
    assert model["has_demo_rules"]
    assert all(summary["is_demo"] for summary in model["rule_summaries"])
    html = render_html(model, tmp_path)
    assert "demo_only" not in html and "grace_minutes" not in html
    assert "示範條款" in html
    assert any(
        issue["code"] == "RULES_CONFIG_INVALID"
        and issue.get("field_path") == "rule_snapshot.match_rules"
        for issue in validate(snapshot, "G5")["issues"]
    )
    snapshot.config["publication"]["layout_template_id"] = "reference-v2"
    with pytest.raises(ValueError, match="demonstration match rules"):
        render_html(view_model(snapshot, draft=False), tmp_path)


def test_v2_graph_metadata_and_portal_group_keys_cover_every_match_once(tmp_path):
    from collections import Counter

    from test_reference_layout import pool_event

    snapshot = pool_event()
    snapshot.config["publication"]["layout_template_id"] = "reference-v2"
    model = view_model(snapshot)
    render_html(model, tmp_path)
    graph_counts = Counter(
        match_id for graph in model["graph_metadata"].values() for match_id in graph["match_ids"]
    )
    assert graph_counts == Counter({match.id: 1 for match in snapshot.matches})
    assert len(model["rule_summaries"]) <= len(snapshot.stages)
    assert [division["id"] for division in model["divisions"]] == [
        division.id for division in snapshot.divisions
    ]


def test_v2_retains_registration_name_in_division_without_matches(tmp_path):
    from squash_scheduler.domain.models import Division, Entry, Participant, Registration

    snapshot = document_event()
    division = Division(name="單人組", status="exhibition_only")
    person = Participant(official_name="單人選手", display_name="單人選手")
    registration = Registration(
        participant_id=person.id,
        original_division_id=division.id,
        source_ref={"source_file_id": "unit-test"},
    )
    snapshot.divisions.append(division)
    snapshot.participants.append(person)
    snapshot.registrations.append(registration)
    snapshot.entries.append(
        Entry(registration_id=registration.id, effective_division_id=division.id)
    )
    snapshot.config["publication"]["layout_template_id"] = "reference-v2"
    model = view_model(snapshot)
    html = render_html(model, tmp_path)
    assert "未編對戰組別" in html
    assert "單人組（1 筆報名）" in html and "單人選手" in html
    assert len(model["matches"]) == len(snapshot.matches)
    assert not any(row["division_id"] == division.id for row in model["matches"])


def test_approved_prospectus_does_not_hide_unresolved_effective_rules(tmp_path):
    from squash_scheduler.exporters.prospectus import SECTION_FIELDS

    snapshot = document_event()
    snapshot.config["publication"].update(
        layout_template_id="reference-v2",
        document_profile="prospectus",
        prospectus={
            "approval_status": "approved",
            **{field: "核准測試條文" for field, _ in SECTION_FIELDS},
        },
    )
    with pytest.raises(ValueError, match="unresolved public match rules"):
        render_html(view_model(snapshot, draft=False), tmp_path)
    assert any(
        issue["code"] == "RULES_CONFIG_MISSING"
        and issue.get("field_path") == "rule_snapshot.match_rules"
        for issue in validate(snapshot, "G5")["issues"]
    )


@pytest.mark.parametrize("change", ["missing_one", "missing_all", "repeated"])
def test_booklet_qa_rejects_incomplete_or_duplicate_graph_coverage(tmp_path, change):
    from squash_scheduler.exporters.documents import document_qa

    snapshot = document_event()
    snapshot.config["publication"]["layout_template_id"] = "reference-v2"
    model = view_model(snapshot)
    render_html(model, tmp_path)
    graph = next(graph for graph in model["graph_metadata"].values() if graph["match_ids"])
    if change == "missing_all":
        del model["graph_metadata"]
    elif change == "missing_one":
        graph["match_ids"].pop()
    else:
        graph["match_ids"].append(graph["match_ids"][0])
    with pytest.raises(ValueError, match="graph coverage"):
        document_qa(model, tmp_path)
