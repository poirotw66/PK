"""Audited entry changes preserve history and reject unsafe identity changes."""

import pytest
from test_formats_draws import event
from test_rankings import result, setup

from squash_scheduler.domain.matches import elimination, generate_matches
from squash_scheduler.domain.models import Entry, Participant, Registration, new_id
from squash_scheduler.domain.rankings import import_results
from squash_scheduler.domain.withdrawals import apply_change, propose_change, register_reserve


def configure_walkovers(snapshot):
    rules = snapshot.config["match_rule_defaults"]
    rules["walkover_rule"] = {
        "include_in_rankings": True,
        "counts_as_played": False,
        "winner_points": 2,
        "loser_points": 0,
    }
    rules["ranking_denominator_rule"] = {"walkover": True}
    # Existing match rule snapshots must explicitly contain these policies.
    for match in snapshot.matches:
        match.rule_snapshot = None


def replacement(snapshot, division):
    person = Participant(official_name="Player 0", display_name="Player 0")
    registration = Registration(
        participant_id=person.id,
        original_division_id=division.id,
        source_ref={"synthetic": "reserve"},
    )
    entry = Entry(registration_id=registration.id, effective_division_id=division.id)
    snapshot.participants.append(person)
    snapshot.registrations.append(registration)
    snapshot.entries.append(entry)
    return entry


def proposal(snapshot, entry, **kwargs):
    return propose_change(
        snapshot, entry.id, reason="Audited organizer decision", actor="operator", **kwargs
    )


def test_cross_division_withdrawal_does_not_touch_another_registration_in_progress():
    snapshot, division, _ = setup(3)
    configure_walkovers(snapshot)
    other, other_division = event(3)
    generate_matches(other)
    other.registrations[0].participant_id = snapshot.participants[0].id
    snapshot.participants.extend(other.participants[1:])
    snapshot.registrations.extend(other.registrations)
    snapshot.entries.extend(other.entries)
    snapshot.divisions.append(other_division)
    snapshot.stages.extend(other.stages)
    other.matches[0].status = "in_progress"
    snapshot.matches.extend(other.matches)
    updated, audit = apply_change(snapshot, proposal(snapshot, snapshot.entries[0]))
    assert all(
        m.division_id == division.id
        for m in updated.matches
        if m.id in audit["impact"]["affected_match_ids"]
    )
    assert next(m for m in updated.matches if m.id == other.matches[0].id).status == "in_progress"
    assert next(e for e in updated.entries if e.id == other.entries[0].id).status == "active"


def test_reserve_keeps_registration_and_can_replace_before_affected_play():
    snapshot, division, _ = setup(3)
    reserve = replacement(snapshot, division)
    registration = next(r for r in snapshot.registrations if r.id == reserve.registration_id)
    source_ref = registration.source_ref.copy()
    result = register_reserve(snapshot, reserve.id, "operator", "Accepted unused reserve")
    assert result["state"] == "reserve" and reserve.status == "cancelled"
    assert registration.status == "accepted" and registration.source_ref == source_ref
    updated, _ = apply_change(
        snapshot,
        proposal(snapshot, snapshot.entries[0], action="replace", replacement_entry_id=reserve.id),
    )
    assert next(e for e in updated.entries if e.id == reserve.id).status == "active"
    with pytest.raises(ValueError, match="already placed"):
        register_reserve(updated, reserve.id, "operator", "Cannot hide a placed entrant")


def test_replacement_may_already_compete_in_a_different_division():
    snapshot, division, _ = setup(3)
    reserve = replacement(snapshot, division)
    register_reserve(snapshot, reserve.id, "operator", "Accepted unused reserve")
    participant_id = next(
        r.participant_id for r in snapshot.registrations if r.id == reserve.registration_id
    )
    other, other_division = event(3)
    generate_matches(other)
    other.registrations[0].participant_id = participant_id
    snapshot.participants.extend(other.participants[1:])
    snapshot.registrations.extend(other.registrations)
    snapshot.entries.extend(other.entries)
    snapshot.divisions.append(other_division)
    snapshot.stages.extend(other.stages)
    other.matches[0].status = "in_progress"
    snapshot.matches.extend(other.matches)
    updated, _ = apply_change(
        snapshot,
        proposal(snapshot, snapshot.entries[0], action="replace", replacement_entry_id=reserve.id),
    )
    assert next(e for e in updated.entries if e.id == reserve.id).status == "active"
    assert next(m for m in updated.matches if m.id == other.matches[0].id).status == "in_progress"


def test_predraw_withdrawal_is_atomic_and_preserves_source_identity():
    snapshot, _division = event(3)
    original = snapshot.model_dump(mode="json")
    change = proposal(snapshot, snapshot.entries[0])
    assert snapshot.model_dump(mode="json") == original
    revised, audit = apply_change(snapshot, change)
    assert revised.entries[0].status == "withdrawn"
    assert snapshot.entries[0].status == "active"
    assert revised.registrations == snapshot.registrations
    assert revised.participants == snapshot.participants
    assert revised.divisions[0].format_plan["status"] == "draft"
    assert revised.changes[-1] == audit
    assert audit["before_hash"] != audit["after_hash"]


def test_remaining_round_robin_walkovers_keep_completed_results_and_codes():
    snapshot, _, _ = setup(3)
    configure_walkovers(snapshot)
    completed = snapshot.matches[0]
    import_results(snapshot, {"results": [result(completed)]})
    withdrawn = next(e for e in snapshot.entries if e.id == completed.side_a.entry_id)
    for index, match in enumerate(snapshot.matches):
        match.display_code = f"M{index + 1}"
    history = snapshot.results.copy()
    codes = {m.id: m.display_code for m in snapshot.matches}
    revised, audit = apply_change(snapshot, proposal(snapshot, withdrawn))
    assert revised.results[: len(history)] == history
    new_results = revised.results[len(history) :]
    assert len(new_results) == 1
    assert new_results[0]["outcome"] == "walkover"
    assert new_results[0]["games"] == []
    assert new_results[0]["winner_id"] != withdrawn.id
    assert {m.id: m.display_code for m in revised.matches} == codes
    assert audit["impact"]["preserved_result_count"] == 1


def test_walkovers_require_explicit_rules_and_no_partial_changes():
    snapshot, _, _ = setup(3)
    original = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError, match="walkover_rule"):
        proposal(snapshot, snapshot.entries[0])
    assert snapshot.model_dump(mode="json") == original


def test_replacement_preserves_match_ids_codes_and_uses_source_ids_not_names():
    snapshot, division, _ = setup(3)
    reserve = replacement(snapshot, division)
    original = snapshot.entries[0]
    before_ids = [m.id for m in snapshot.matches]
    snapshot.matches[0].display_code = "KEEP"
    revised, audit = apply_change(
        snapshot, proposal(snapshot, original, action="replace", replacement_entry_id=reserve.id)
    )
    assert [m.id for m in revised.matches] == before_ids
    assert revised.matches[0].display_code == "KEEP"
    assert revised.entries[0].status == "withdrawn"
    assert audit["impact"]["rebound_match_ids"]
    assert all(s.entry_id != original.id for m in revised.matches for s in (m.side_a, m.side_b))
    assert snapshot.entries[0].status == "active"


def test_replacement_rejects_played_history_and_already_participating_entry():
    snapshot, division, _ = setup(3)
    reserve = replacement(snapshot, division)
    with pytest.raises(ValueError, match="already participates"):
        proposal(
            snapshot,
            snapshot.entries[0],
            action="replace",
            replacement_entry_id=snapshot.entries[1].id,
        )
    completed = snapshot.matches[0]
    import_results(snapshot, {"results": [result(completed)]})
    entry = next(e for e in snapshot.entries if e.id == completed.side_a.entry_id)
    with pytest.raises(ValueError, match="started matches"):
        proposal(snapshot, entry, action="replace", replacement_entry_id=reserve.id)


@pytest.mark.parametrize("mutation", ["content", "approval", "proposal"])
def test_stale_or_modified_proposals_are_rejected(mutation):
    snapshot, _ = event(3)
    change = proposal(snapshot, snapshot.entries[0])
    if mutation == "content":
        snapshot.participants[0].display_name = "Revised"
    elif mutation == "approval":
        snapshot.approvals.append({"id": new_id(), "action": "approve"})
    else:
        change["operation"]["reason"] = "Modified after review"
    with pytest.raises(ValueError, match="stale|modified"):
        apply_change(snapshot, change)


def test_in_progress_match_blocks_withdrawal():
    snapshot, _, _ = setup(3)
    snapshot.matches[0].status = "in_progress"
    entry = next(e for e in snapshot.entries if e.id == snapshot.matches[0].side_a.entry_id)
    with pytest.raises(ValueError, match="in progress"):
        proposal(snapshot, entry)


def test_elimination_withdrawal_awards_real_opponent_without_bye_or_score():
    snapshot, division, stage = setup(3)
    configure_walkovers(snapshot)
    stage.type = "elimination"
    entries = [e.id for e in snapshot.entries[:2]]
    snapshot.matches = elimination(entries, division.id, stage.id)
    withdrawn = snapshot.entries[0]
    revised, _ = apply_change(snapshot, proposal(snapshot, withdrawn))
    assert len(revised.matches) == 1
    assert revised.matches[0].status == "completed"
    assert revised.results[0]["winner_id"] == entries[1]
    assert revised.results[0]["games"] == []
    assert all(s.type == "entry" for s in (revised.matches[0].side_a, revised.matches[0].side_b))


def test_unresolved_elimination_opponent_requires_review_without_partial_edit():
    snapshot, division, stage = setup(4)
    configure_walkovers(snapshot)
    stage.type = "elimination"
    entries = [e.id for e in snapshot.entries]
    # Three players produce a bye advancing the third player to the final.
    snapshot.matches = elimination(entries[:3], division.id, stage.id)
    final = snapshot.matches[-1]
    direct = next(s.entry_id for s in (final.side_a, final.side_b) if s.type == "entry")
    withdrawn = next(e for e in snapshot.entries if e.id == direct)
    before = snapshot.model_dump(mode="json")
    with pytest.raises(ValueError, match="opponent is unresolved"):
        proposal(snapshot, withdrawn)
    assert snapshot.model_dump(mode="json") == before
