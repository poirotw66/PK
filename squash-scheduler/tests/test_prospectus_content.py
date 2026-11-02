"""Public terms preserve unknowns, demonstration status and division differences."""

from copy import deepcopy

import pytest
from test_rankings import setup

from squash_scheduler.domain.config import Publication
from squash_scheduler.exporters.prospectus import (
    DEMO_PENDING,
    PENDING,
    SECTION_FIELDS,
    contains_demo_rule,
    merge_rule_summaries,
    prospectus_view,
    public_rule_text,
    publication_content_issues,
    rule_summary,
)


def complete_config():
    return {
        "publication": {
            "document_profile": "prospectus",
            "prospectus": {
                "approval_status": "approved",
                **{field: "主辦已核定之測試內容" for field, _ in SECTION_FIELDS},
            },
        }
    }


def test_legacy_publication_and_new_prospectus_contract():
    assert Publication().document_profile == "order_book"
    assert publication_content_issues({"publication": {}}) == []
    content = complete_config()
    parsed = Publication.model_validate(content["publication"])
    assert parsed.prospectus.approval_status == "approved"
    assert publication_content_issues(content) == []
    del content["publication"]["prospectus"]["registration"]
    assert publication_content_issues(content) == ["publication.prospectus.registration"]


@pytest.mark.parametrize("placeholder", ["待補", "請待確認後公布", "尚未設定", "TODO", "TBD"])
def test_approval_does_not_make_missing_terms_publishable(placeholder):
    config = complete_config()
    config["publication"]["prospectus"]["awards"] = placeholder
    assert "publication.prospectus.awards" in publication_content_issues(config)
    view = prospectus_view(config)
    assert not view["complete"]
    assert next(s for s in view["sections"] if s["field"] == "awards")["lines"] == [PENDING]


def test_draft_has_explicit_unknown_sections_and_no_invented_terms():
    view = prospectus_view({"publication": {"document_profile": "prospectus"}})
    assert len(view["sections"]) == len(SECTION_FIELDS)
    assert all(section["lines"] == [PENDING] for section in view["sections"])
    assert not view["complete"] and not view["approved"]


def test_public_rule_allowlist_does_not_serialize_engineering_metadata():
    assert public_rule_text(
        {"grace_minutes": 5, "action": "manual_review", "automatic": False}
    ) == ("遲到寬限分鐘：5；處置：由賽務人員處理；自動處理：否")
    assert public_rule_text({"secret_internal_value": "internal"}) == PENDING
    assert public_rule_text({"demo_only": True, "grace_minutes": 5}) == DEMO_PENDING
    assert public_rule_text("grace_minutes：5；demo_only：是") == PENDING


def test_automatic_semantics_prevent_merging_and_unknown_terms_stay_pending():
    rules = [{"mode": "manual_review", "automatic": automatic} for automatic in (True, False)]
    merged = merge_rule_summaries(
        [
            {"title": title, "lines": [public_rule_text(rule)]}
            for title, rule in zip(("甲組", "乙組"), rules, strict=True)
        ]
    )
    assert len(merged) == 2
    assert merged[0]["lines"] == ["方式：由賽務人員處理；自動處理：是"]
    assert merged[1]["lines"] == ["方式：由賽務人員處理；自動處理：否"]
    assert public_rule_text({"mode": "manual_review", "untranslated_terms": "material"}) == (
        "方式：由賽務人員處理；" + PENDING
    )
    assert public_rule_text({"automatic": None}) == "自動處理：" + PENDING
    assert public_rule_text({"mode": "manual_review", "synthetic_only": False}) == (
        "方式：由賽務人員處理"
    )
    assert public_rule_text({"mode": "manual_review", "synthetic_only": True}) == (
        "方式：由賽務人員處理"
    )


def test_synthetic_provenance_does_not_make_explicit_rules_unapproved():
    synthetic = {"action": "manual_review", "automatic": False, "synthetic_only": True}
    assert not contains_demo_rule({"replacement_rule": synthetic})
    assert public_rule_text(synthetic) == "處置：由賽務人員處理；自動處理：否"
    unapproved_demo = {**synthetic, "demo_only": True}
    assert contains_demo_rule({"replacement_rule": unapproved_demo})
    assert public_rule_text(unapproved_demo) == DEMO_PENDING
    snapshot, _, _ = setup()
    for match in snapshot.matches:
        match.rule_snapshot["match_rules"]["replacement_rule"] = deepcopy(synthetic)
    assert not any(summary["is_demo"] for summary in rule_summary(snapshot))
    snapshot.matches[0].rule_snapshot["match_rules"]["replacement_rule"] = unapproved_demo
    assert any(summary["is_demo"] for summary in rule_summary(snapshot))


def test_merge_keeps_distinct_effective_rules_and_scope_order():
    summaries = [
        {"title": "甲組 · 循環賽", "lines": ["3 局 2 勝"]},
        {"title": "乙組 · 循環賽", "lines": ["3 局 2 勝"]},
        {"title": "乙組 · 淘汰賽", "lines": ["5 局 3 勝"]},
        {"title": "乙組 · 循環賽", "lines": ["3 局 2 勝"]},
    ]
    before = deepcopy(summaries)
    merged = merge_rule_summaries(summaries)
    assert len(merged) == 2
    assert merged[0]["scopes"] == ["甲組 · 循環賽", "乙組 · 循環賽"]
    assert merged[1]["lines"] == ["5 局 3 勝"]
    assert summaries == before


def test_frozen_rules_have_no_invented_tie_order_and_preserve_demo_evidence():
    snapshot, _, _ = setup()
    summary = rule_summary(snapshot)
    assert len(summary) == 1
    assert summary[0]["lines"][0] == "3 局 2 勝"
    assert summary[0]["lines"][-1] == "同分比較：" + PENDING
    snapshot.matches[0].rule_snapshot["match_rules"]["late_arrival_rule"] = {
        "demo_only": True,
        "grace_minutes": 5,
        "action": "manual_review",
    }
    snapshot.config["match_rule_defaults"]["best_of_games"] = 5
    summaries = rule_summary(snapshot)
    assert len(summaries) == 2
    demo = next(s for s in summaries if s["is_demo"])
    assert demo["lines"][0] == DEMO_PENDING
    assert "3 局 2 勝" in demo["lines"]
    assert not any("demo_only" in line or "grace_minutes" in line for line in demo["lines"])
