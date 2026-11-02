"""Qualification gates and review history must describe the current state accurately."""

from openpyxl import load_workbook
from test_review_decisions import reviewed_event

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Issue, new_id
from squash_scheduler.domain.rankings import import_results
from squash_scheduler.domain.validation import validate
from squash_scheduler.exporters.documents import export_excel, render_html, view_model


def test_g6_blocks_unconfirmed_results_without_requiring_schedule_or_publication():
    snapshot, _ = reviewed_event(3)
    generate_matches(snapshot)
    before_results = validate(snapshot, "G6")
    assert not before_results["passed"]
    assert any(i["code"] == "RANKING_RESULTS_PENDING" for i in before_results["issues"])
    assert not any(
        i["code"] in {"PUBLICATION_CONFIG_MISSING", "TIMING_CONFIG_MISSING"}
        for i in before_results["issues"]
    )
    import_results(
        snapshot,
        {
            "actor_id": "synthetic-scorekeeper",
            "results": [
                {
                    "match_id": m.id,
                    "outcome": "played",
                    "status": "confirmed",
                    "games": [{"side_a": 11, "side_b": 3}],
                    "winner_id": m.side_a.entry_id,
                }
                for m in snapshot.matches
            ],
        },
    )
    assert not snapshot.assignments
    assert snapshot.config["publication"]["font_asset_id"] is None
    assert validate(snapshot, "G6")["passed"]
    assert not validate(snapshot, "G5")["passed"]


def test_resolved_issues_remain_in_history_but_not_pending_document_section(tmp_path):
    snapshot, _ = reviewed_event(3)
    generate_matches(snapshot)
    snapshot.issues.append(
        Issue(
            code="IDENTITY_AMBIGUOUS",
            message="Synthetic resolved identity",
            status="resolved",
            resolution_id=new_id(),
        )
    )
    model = view_model(snapshot)
    assert model["issues"] == [] and len(model["issue_history"]) == 1
    html = render_html(model, tmp_path)
    assert "待處理事項" not in html
    export_excel(snapshot, model, tmp_path / "schedule.xlsx")
    wb = load_workbook(tmp_path / "schedule.xlsx")
    headers = [cell.value for cell in wb["Issues"][1]]
    row = dict(zip(headers, next(wb["Issues"].iter_rows(min_row=2, values_only=True))))
    assert row["code"] == "IDENTITY_AMBIGUOUS" and row["status"] == "resolved"
    snapshot.issues.append(
        Issue(code="DRAW_CONSTRAINT_UNRESOLVED", message="Synthetic pending rule")
    )
    html = render_html(view_model(snapshot), tmp_path)
    assert "待處理事項" in html and "特殊抽位規則尚未明確" in html
