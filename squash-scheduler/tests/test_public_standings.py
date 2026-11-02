"""Round-robin public standings reuse authoritative scoring and approval gates."""

import json

from test_review_decisions import normal_result, reviewed_event

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import new_id
from squash_scheduler.domain.rankings import import_results, ranking_basis
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis
from squash_scheduler.public_feed import PublicSnapshot, project_snapshot


def round_robin(count=3):
    snapshot, division = reviewed_event(count)
    snapshot.config["match_rule_defaults"].update(
        walkover_rule={
            "include_in_rankings": True,
            "counts_as_played": False,
            "winner_points": 2,
            "loser_points": 0,
        },
        ranking_denominator_rule={"walkover": True},
    )
    generate_matches(snapshot)
    return snapshot, division, snapshot.stages[0]


def test_live_standings_are_provisional_until_all_confirmed_results_exist():
    snapshot, _, _ = round_robin()
    public = project_snapshot(snapshot, "scope")
    assert public.publication_status == "ready"
    group = public.standings[0]
    assert group.status == "provisional"
    assert group.confirmed_matches == 0 and group.total_matches == 3
    assert all(row.rank is None and row.points == 0 and not row.qualifies for row in group.rows)
    import_results(snapshot, {"results": [normal_result(snapshot.matches[0])]})
    group = project_snapshot(snapshot, "scope").standings[0]
    assert group.status == "provisional" and group.confirmed_matches == 1
    assert sum(row.points for row in group.rows) == 3
    assert "勝:2分 敗:1分 棄:0分" == group.scoring_label


def test_walkover_points_and_counts_come_from_rules_without_fabricated_games():
    snapshot, _, _ = round_robin()
    ordered = sorted(e.id for e in snapshot.entries)
    batch = [normal_result(m) for m in snapshot.matches]
    walk = next(
        r
        for m, r in zip(snapshot.matches, batch, strict=True)
        if {m.side_a.entry_id, m.side_b.entry_id} == {ordered[0], ordered[2]}
    )
    walk.update(outcome="walkover", games=[])
    import_results(snapshot, {"results": batch})
    public = project_snapshot(snapshot, "scope")
    group = public.standings[0]
    assert group.status == "final"
    assert [row.rank for row in group.rows] == [1, 2, 3]
    assert [row.points for row in group.rows] == [4, 3, 1]
    assert [row.played_count for row in group.rows] == [1, 2, 1]
    assert group.rows[-1].forfeits == 1 and group.rows[-1].losses == 2
    assert not next(m for m in public.matches if m.id == walk["match_id"]).result.games


def test_three_way_tie_requires_current_manual_approval_and_never_exposes_reasons():
    snapshot, _, stage = round_robin()
    entries = sorted(e.id for e in snapshot.entries)
    wins = {entries[0]: entries[1], entries[1]: entries[2], entries[2]: entries[0]}
    import_results(
        snapshot,
        {
            "results": [
                normal_result(m, wins[m.side_a.entry_id] == m.side_b.entry_id)
                for m in snapshot.matches
            ]
        },
    )
    group = project_snapshot(snapshot, "scope").standings[0]
    assert group.status == "manual_review" and "同分待裁定" in group.notice
    assert all(row.rank is None and not row.qualifies for row in group.rows)
    secret = "PRIVATE-ARBITRATION-REASON"
    decision = {
        "id": new_id(),
        "stage_id": stage.id,
        "pool_id": None,
        "ordered_entry_ids": list(reversed(entries)),
        "basis_hash": ranking_basis(snapshot, stage.id),
        "reason": secret,
        "status": "draft",
        "approval_id": None,
    }
    snapshot.ranking_decisions.append(decision)
    assert project_snapshot(snapshot, "scope").standings[0].status == "manual_review"
    snapshot, _ = approve(
        snapshot, f"ranking:{decision['id']}", target_basis(decision), "reviewer", secret
    )
    public = project_snapshot(snapshot, "scope")
    assert public.standings[0].status == "final"
    assert [row.entry_id for row in public.standings[0].rows] == list(reversed(entries))
    assert secret not in public.model_dump_json()


def test_custom_scoring_is_not_replaced_by_default_points():
    snapshot, division, _ = round_robin()
    division.rule_overrides["scoring"] = {"win_points": 5, "loss_points": 2, "forfeit_points": 0}
    generate_matches(snapshot)
    import_results(snapshot, {"results": [normal_result(m) for m in snapshot.matches]})
    group = project_snapshot(snapshot, "scope").standings[0]
    assert [row.points for row in group.rows] == [10, 7, 4]
    assert group.scoring_label.startswith("勝:5分 敗:2分")


def test_public_schema_addition_is_optional_for_existing_snapshot_providers():
    snapshot, _, _ = round_robin()
    value = project_snapshot(snapshot, "scope").model_dump(mode="json")
    value.pop("standings")
    assert PublicSnapshot.model_validate(value).standings == []
    assert (
        json.loads(project_snapshot(snapshot, "scope").model_dump_json())["schema_version"]
        == "1.0.0"
    )
