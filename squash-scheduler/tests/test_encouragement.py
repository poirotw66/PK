"""Confirmed junior-high rule excludes seeds, byes, walkovers and later losers."""

import pytest
from test_formats_draws import approve_draw, event

from squash_scheduler.cli import parser
from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.encouragement import eligibility_report
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import ByeSide, EntrySide, content_hash


def example(size=8):
    snapshot, division = event(size, "single_elimination")
    division.name, division.category = "國中男子組", "school"
    if size == 6:
        snapshot.config["draw_defaults"]["bye_allocation_rule"] = {"positions": [2, 6]}
    approve_draw(run_draw(snapshot, division.id, 42, "tester"))
    generate_matches(snapshot)
    first = [m for m in snapshot.matches if m.round == 1 and m.kind == "competitive"]
    for match in first:
        if isinstance(match.side_a, EntrySide) and isinstance(match.side_b, EntrySide):
            match.status = "completed"
            snapshot.results.append(
                {
                    "match_id": match.id,
                    "status": "confirmed",
                    "outcome": "played",
                    "winner_id": match.side_a.entry_id,
                }
            )
    return snapshot, division, first


@pytest.mark.parametrize("name", ["國中男子組", "國中女子組"])
def test_first_round_played_losers_only_and_report_does_not_mutate(name):
    snapshot, division, first = example()
    division.name = name
    before = content_hash(snapshot)
    report = eligibility_report(snapshot, division.id)
    assert set(report["eligible_entry_ids"]) == {m.side_b.entry_id for m in first}
    assert not report["pending_entry_ids"]
    assert content_hash(snapshot) == before


@pytest.mark.parametrize("name", ["高中男子組", "國小女子組", "男子A組"])
def test_other_divisions_are_ineligible(name):
    snapshot, division, _ = example()
    division.name = name
    report = eligibility_report(snapshot, division.id)
    assert not report["eligible_entry_ids"] and not report["pending_entry_ids"]


def test_seeded_loser_is_excluded_and_unknown_seeds_do_not_qualify_anyone():
    snapshot, division, first = example()
    seeded = first[0].side_b.entry_id
    division.draw_overrides = {
        "seeded_entries_mode": "provided",
        "seeded_entries": [{"entry_id": seeded, "seed_number": 1}],
    }
    report = eligibility_report(snapshot, division.id)
    assert seeded not in report["eligible_entry_ids"]
    assert next(r for r in report["entries"] if r["entry_id"] == seeded)["reason"] == "SEEDED_ENTRY"
    division.draw_overrides = {"seeded_entries_mode": "unconfigured", "seeded_entries": []}
    assert not eligibility_report(snapshot, division.id)["eligible_entry_ids"]


@pytest.mark.parametrize("outcome", ["walkover", "double_forfeit", "retired", "pending"])
def test_special_outcomes_do_not_qualify_as_played_loss(outcome):
    snapshot, division, first = example()
    snapshot.results[0]["outcome"] = outcome
    report = eligibility_report(snapshot, division.id)
    assert first[0].side_b.entry_id not in report["eligible_entry_ids"]


def test_bye_and_later_round_loss_never_qualify():
    snapshot, division, first = example()
    entrant = first[0].side_a.entry_id
    first[0].side_b = ByeSide()
    report = eligibility_report(snapshot, division.id)
    assert entrant not in report["eligible_entry_ids"]
    assert (
        next(r for r in report["entries"] if r["entry_id"] == entrant)["reason"]
        == "FIRST_ROUND_BYE"
    )
    # A later-round result does not change first-round eligibility.
    later = next(m for m in snapshot.matches if m.round == 2)
    later.status = "completed"
    snapshot.results.append(
        {
            "match_id": later.id,
            "status": "confirmed",
            "outcome": "played",
            "winner_id": first[1].side_a.entry_id,
        }
    )
    assert entrant not in eligibility_report(snapshot, division.id)["eligible_entry_ids"]


def test_pending_and_conflicting_results_remain_unresolved():
    snapshot, division, first = example()
    first[0].status = "planned"
    snapshot.results.append(dict(snapshot.results[1]))
    report = eligibility_report(snapshot, division.id)
    assert {first[0].side_b.entry_id, first[1].side_b.entry_id} <= set(report["pending_entry_ids"])


def test_real_six_player_bracket_never_qualifies_bye_recipients():
    snapshot, division, first = example(6)
    played = [
        m for m in first if isinstance(m.side_a, EntrySide) and isinstance(m.side_b, EntrySide)
    ]
    report = eligibility_report(snapshot, division.id)
    assert set(report["eligible_entry_ids"]) == {m.side_b.entry_id for m in played}
    assert len(report["eligible_entry_ids"]) == 2


@pytest.mark.parametrize("seeds", [None, [{"entry_id": []}], [{"entry_id": "unknown"}]])
def test_malformed_or_foreign_seed_data_cannot_qualify_players(seeds):
    snapshot, division, _ = example()
    division.draw_overrides = {"seeded_entries_mode": "provided", "seeded_entries": seeds}
    assert not eligibility_report(snapshot, division.id)["eligible_entry_ids"]


def test_report_cli_accepts_division_and_workspace():
    args = parser().parse_args(
        ["encouragement", "report", "--workspace", "example", "--division", "division-id", "--json"]
    )
    assert args.group == "encouragement" and args.division == "division-id"
