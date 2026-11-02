"""Qualified draws are reproducible, confirmed, guarded and usable by the scheduler."""

import pytest
from test_formats_draws import event
from test_rankings import result
from test_scheduling import fixture

from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.encouragement_draws import (
    apply_draw,
    propose_draw,
    validate_encouragement,
)
from squash_scheduler.domain.matches import generate_matches, validate_graph
from squash_scheduler.domain.models import LoserSide, content_hash
from squash_scheduler.domain.rankings import import_results, resolve_side
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis
from squash_scheduler.exporters.documents import build_documents, export_excel, view_model
from squash_scheduler.exporters.encouragement_proposal import render_proposal
from squash_scheduler.scheduling import solve_schedule, validate_schedule


def qualified(eligible=4, size=8):
    snapshot, division = event(size, "single_elimination")
    snapshot.config.update(fixture(courts=4, rest=15, end="18:00").config)
    snapshot.config["draw_defaults"] = {
        "seeded_entries_mode": "none",
        "affiliation_avoidance_mode": "disabled",
    }
    snapshot.config["match_rule_defaults"] = {
        "best_of_games": 3,
        "points_to_win_game": 11,
        "win_by_points": 2,
        "point_cap_mode": "none",
        "point_cap": None,
    }
    division.name, division.category, division.gender_category = "國中男子組", "school", "male"
    seeded = [e.id for e in snapshot.entries[: 4 - eligible]] if size == 8 else []
    if seeded:
        snapshot.config["draw_defaults"].update(
            seeded_entries_mode="provided",
            seeded_entries=[
                {"entry_id": eid, "seed_number": i + 1, "allowed_positions": [str(2 * i + 2)]}
                for i, eid in enumerate(seeded)
            ],
        )
    if size == 6:
        snapshot.config["draw_defaults"]["bye_allocation_rule"] = {"positions": [2, 6]}
    snapshot, _ = approve(
        snapshot,
        f"format:{division.id}",
        target_basis(division.format_plan),
        "operator",
        "Review format",
    )
    draw = run_draw(snapshot, division.id, 42, "operator")
    snapshot, _ = approve(
        snapshot, f"draw:{draw['id']}", target_basis(draw), "operator", "Review draw"
    )
    generate_matches(snapshot)
    initial_schedule = solve_schedule(snapshot, time_limit_seconds=10)
    assert initial_schedule["status"] in {"FEASIBLE", "OPTIMAL"}, initial_schedule
    first = [m for m in snapshot.matches if m.kind == "competitive" and m.round == 1]
    import_results(
        snapshot,
        {
            "results": [result(m, winner_a=m.side_a.entry_id not in seeded) for m in first],
            "actor_id": "operator",
        },
    )
    return snapshot, division.id


def confirm(snapshot, division_id, seed=7):
    proposal = propose_draw(snapshot, division_id, seed, "operator", "Draw qualified players")
    updated, audit = apply_draw(
        snapshot, proposal, proposal["proposal_hash"], "reviewer", "Confirm printed proposal"
    )
    return updated, proposal, audit


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_count_byes_and_no_competition_are_explicit(count):
    snapshot, division_id = qualified(count)
    before = content_hash(snapshot)
    updated, proposal, audit = confirm(snapshot, division_id)
    assert content_hash(snapshot) == before
    assert len(audit["entry_ids"]) == count
    assert len(audit["match_ids"]) == max(0, count - 1)
    assert proposal["output"]["disposition"] == ("draw" if count >= 2 else "no_competition")
    assert len(proposal["output"]["bye_positions"]) == (1 if count == 3 else 0)
    assert validate_encouragement(updated) == []
    assert validate_graph(updated) == []
    if count >= 2:
        assert sorted(
            resolve_side(updated, side)
            for m in updated.matches
            if m.id in audit["match_ids"]
            for side in (m.side_a, m.side_b)
            if isinstance(side, LoserSide)
        ) == sorted(audit["entry_ids"])
    generated = updated.model_dump(mode="json")
    generate_matches(updated)
    assert updated.model_dump(mode="json") == generated


def test_same_seed_has_same_bracket_and_proposal_does_not_mutate():
    snapshot, division_id = qualified()
    before = snapshot.model_dump(mode="json")
    first = propose_draw(snapshot, division_id, 13, "operator", "Test")
    second = propose_draw(snapshot, division_id, 13, "operator", "Test")
    assert first["output"] == second["output"]
    assert snapshot.model_dump(mode="json") == before


@pytest.mark.parametrize("change", ["hash", "tamper", "revision", "seeds", "result", "assignment"])
def test_stale_or_tampered_proposals_are_rejected_atomically(change):
    snapshot, division_id = qualified()
    proposal = propose_draw(snapshot, division_id, 1, "operator", "Review")
    supplied = proposal["proposal_hash"]
    if change == "hash":
        supplied = "0" * 64
    elif change == "tamper":
        proposal["output"]["entry_ids"].pop()
    elif change == "revision":
        proposal["base_revision_id"] = division_id
    elif change == "seeds":
        snapshot.config["draw_defaults"]["seeded_entries_mode"] = "unconfigured"
    elif change == "result":
        source = next(m for m in snapshot.matches if m.id == snapshot.results[0]["match_id"])
        snapshot.results[0]["winner_id"] = source.side_b.entry_id
    else:
        snapshot.config["timing_defaults"]["minimum_player_rest_minutes"] += 1
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError):
        apply_draw(snapshot, proposal, supplied, "operator", "Confirm")
    assert snapshot.model_dump(mode="json") == before


def test_qualifications_becoming_stale_block_graph_solver_and_every_snapshot_export(tmp_path):
    snapshot, division_id = qualified()
    updated, _, _ = confirm(snapshot, division_id)
    updated.config["draw_defaults"]["seeded_entries_mode"] = "unconfigured"
    assert any(i.code == "ENCOURAGEMENT_INVALID" for i in validate_graph(updated))
    assert solve_schedule(updated)["status"] == "MODEL_INVALID"
    with pytest.raises(ValueError, match="ENCOURAGEMENT"):
        view_model(updated)
    with pytest.raises(ValueError, match="ENCOURAGEMENT"):
        export_excel(updated, {}, tmp_path / "bad.xlsx")
    with pytest.raises(ValueError, match="ENCOURAGEMENT"):
        build_documents(updated, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_draw_applies_after_confirmed_sources_and_schedules_with_rest():
    snapshot, division_id = qualified(3)
    updated, _, audit = confirm(snapshot, division_id)
    assert solve_schedule(updated, time_limit_seconds=10)["status"] in {"FEASIBLE", "OPTIMAL"}
    assert not any(i.severity == "error" for i in validate_schedule(updated))
    assert len(updated.assignments) == len(updated.matches) == 9
    assert len(audit["match_ids"]) == 2


@pytest.mark.parametrize("protected", ["started", "result", "lock"])
def test_protected_redraws_are_atomic(protected):
    snapshot, division_id = qualified()
    updated, _, audit = confirm(snapshot, division_id)
    if protected == "started":
        next(m for m in updated.matches if m.id in audit["match_ids"]).status = "in_progress"
    elif protected == "result":
        updated.results.append({"match_id": audit["match_ids"][0], "status": "pending"})
    else:
        assert solve_schedule(updated, time_limit_seconds=10)["status"] in {"FEASIBLE", "OPTIMAL"}
        next(a for a in updated.assignments if a.match_id in audit["match_ids"]).locks["court"] = (
            True
        )
    proposal = propose_draw(updated, division_id, 19, "operator", "Redraw")
    before = updated.model_dump(mode="json")
    with pytest.raises(ValueError, match="REDRAW_LOCKED"):
        apply_draw(updated, proposal, proposal["proposal_hash"], "operator", "Confirm")
    assert updated.model_dump(mode="json") == before


def test_six_player_main_bye_recipients_are_not_in_draw():
    snapshot, division_id = qualified(size=6)
    updated, proposal, audit = confirm(snapshot, division_id)
    assert len(audit["entry_ids"]) == 2
    assert len(audit["match_ids"]) == 1
    assert len(proposal["output"]["qualification_report"]["entries"]) == 6
    assert not validate_encouragement(updated)


def test_pending_first_round_or_unapproved_source_cannot_propose():
    snapshot, division_id = qualified()
    incomplete = snapshot.model_copy(deep=True)
    incomplete.results.pop()
    with pytest.raises(ValueError, match="PENDING"):
        propose_draw(incomplete, division_id, 1, "operator", "Test")
    snapshot.approvals.clear()
    with pytest.raises(ValueError, match="UNAPPROVED"):
        propose_draw(snapshot, division_id, 1, "operator", "Test")


def test_girls_draw_uses_same_rule_and_readable_labels():
    snapshot, division_id = qualified(3)
    snapshot.divisions[0].name, snapshot.divisions[0].gender_category = "國中女子組", "female"
    updated, proposal, audit = confirm(snapshot, division_id)
    assert len(audit["entry_ids"]) == 3
    assert all(m["code"].startswith("國女鼓") for m in proposal["output"]["preview"]["matches"])
    assert not validate_encouragement(updated)


def test_manual_definition_cannot_bypass_eligibility_or_approval():
    snapshot, division_id = qualified()
    division = snapshot.divisions[0]
    source = next(m for m in snapshot.matches if m.round == 1)
    division.format_plan["placement_matches"] = [
        {
            "id": division_id,
            "label": "鼓勵賽",
            "side_a": source.side_a.model_dump(),
            "side_b": source.side_b.model_dump(),
            "round": 1,
            "bracket_position": 0,
        }
    ]
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError, match="ENCOURAGEMENT"):
        generate_matches(snapshot)
    assert snapshot.model_dump(mode="json") == before
    assert validate_encouragement(snapshot)


@pytest.mark.parametrize("mutation", ["action", "stage", "sides", "output"])
def test_confirmed_receipt_and_geometry_cannot_be_tampered(mutation):
    snapshot, division_id = qualified()
    updated, _, audit = confirm(snapshot, division_id)
    if mutation == "action":
        updated.approvals[-1]["action"] = "reject"
    elif mutation == "stage":
        next(s for s in updated.stages if s.stage_key == "encouragement").label = "Renamed"
    elif mutation == "sides":
        target = next(m for m in updated.matches if m.id in audit["match_ids"])
        target.side_a = target.side_b
    else:
        updated.draws[-1]["output"]["entry_ids"].pop()
    assert validate_encouragement(updated)


def test_unstarted_redraw_preserves_main_schedule_and_clears_only_encouragement():
    snapshot, division_id = qualified()
    updated, _, audit = confirm(snapshot, division_id)
    assert solve_schedule(updated, time_limit_seconds=10)["status"] in {"FEASIBLE", "OPTIMAL"}
    before_main = [
        a.model_dump() for a in updated.assignments if a.match_id not in audit["match_ids"]
    ]
    redrawn, _, replacement = confirm(updated, division_id, seed=91)
    assert [a.model_dump() for a in redrawn.assignments] == before_main
    assert set(replacement["cleared_assignment_ids"]) == set(audit["match_ids"])
    assert redrawn.draws[-2]["status"] == "superseded"
    assert not validate_encouragement(redrawn)


def test_first_round_correction_requires_fresh_draw_and_can_be_repaired_before_play():
    snapshot, division_id = qualified()
    updated, _, _ = confirm(snapshot, division_id)
    match = next(m for m in updated.matches if m.round == 1 and m.kind == "competitive")
    import_results(
        updated,
        {
            "results": [result(match, winner_a=False)],
            "actor_id": "operator",
            "reason": "Correct score",
        },
    )
    assert validate_encouragement(updated)
    repaired, _, _ = confirm(updated, division_id, seed=22)
    assert not validate_encouragement(repaired)


def test_proposal_html_lists_names_byes_and_escapes_participant_text():
    snapshot, division_id = qualified(3)
    snapshot.participants[0].display_name = "<script>alert(1)</script>"
    proposal = propose_draw(snapshot, division_id, 9, "operator", "Review")
    html = render_proposal(proposal)
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "輪空" in html and "種子球員" in html
    assert proposal["proposal_hash"] in html
