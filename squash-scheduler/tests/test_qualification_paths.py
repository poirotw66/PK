"""Independently enumerate real brackets; never select favorable outcomes to schedule."""

from itertools import product

import pytest
from test_scheduling import assign, fixture, pooled_fixture, successful

from squash_scheduler.domain.matches import elimination
from squash_scheduler.domain.models import (
    Entry,
    Participant,
    PoolRankSide,
    Registration,
    content_hash,
    new_id,
)
from squash_scheduler.scheduling import solve_schedule, validate_schedule
from squash_scheduler.scheduling.paths import PathAnalysisIncomplete, analyze_paths


def bracket(count=4, bronze=True):
    s = fixture(courts=2)
    division, stage = s.divisions[0], s.stages[0]
    s.divisions, s.stages = [division], [stage]
    for registration in s.registrations:
        registration.original_division_id = division.id
    for entry in s.entries:
        entry.effective_division_id = division.id
    for i in range(4, count):
        person = Participant(official_name=f"Player {i}", display_name=f"Player {i}")
        reg = Registration(
            participant_id=person.id,
            original_division_id=division.id,
            source_ref={"file": "synthetic"},
        )
        s.participants.append(person)
        s.registrations.append(reg)
        s.entries.append(Entry(registration_id=reg.id, effective_division_id=division.id))
    s.matches = elimination([e.id for e in s.entries], division.id, stage.id, bronze=bronze)
    return s


@pytest.mark.parametrize("count", [4, 8])
def test_paths_match_exhaustive_outcomes_and_final_is_exclusive_with_bronze(count):
    s = bracket(count)
    analysis = analyze_paths(s)
    registrations = {r.id: r.participant_id for r in s.registrations}
    owners = {e.id: registrations[e.registration_id] for e in s.entries}
    observed = {p.id: set() for p in s.participants}
    # This separate simulator follows actual winner/loser identities for EVERY outcome.
    for outcomes in product((0, 1), repeat=len(s.matches)):
        winners, losers = {}, {}
        played = {p.id: set() for p in s.participants}
        for match, outcome in zip(s.matches, outcomes, strict=True):

            def resolve(side, winners=winners, losers=losers):
                if side.type == "entry":
                    return side.entry_id
                return (winners if side.type == "match_winner" else losers)[side.match_id]

            pair = [resolve(match.side_a), resolve(match.side_b)]
            winners[match.id], losers[match.id] = pair[outcome], pair[1 - outcome]
            for entry in pair:
                played[owners[entry]].add(match.id)
        for person, path in played.items():
            observed[person].add(frozenset(path))
    for person, paths in observed.items():
        maximal = {p for p in paths if not any(p < other for other in paths)}
        assert set(analysis.paths[person]) == maximal
    final = next(
        m for m in s.matches if m.kind != "placement" and m.round == max(x.round for x in s.matches)
    )
    bronze = next(m for m in s.matches if m.kind == "placement")
    assert not analysis.shared(final.id, bronze.id)


def test_fixed_ranks_from_one_pool_are_mutually_exclusive_not_extra_appearances():
    s = pooled_fixture()
    m = s.matches[-1]
    s.matches.append(
        m.model_copy(update={"id": new_id(), "side_a": PoolRankSide(pool_id=s.pools[0].id, rank=2)})
    )
    analysis = analyze_paths(s)
    person = s.participants[0].id
    assert analysis.reports[person]["raw_candidate_count"] == 4
    assert analysis.reports[person]["maximum_topology_matches"] == 3
    assert person not in analysis.shared(s.matches[-1].id, s.matches[-2].id)


def test_confirmed_winner_cannot_take_the_loser_branch():
    s = bracket()
    first = s.matches[0]
    person = s.participants[0].id
    s.results = [
        {
            "match_id": first.id,
            "status": "confirmed",
            "outcome": "played",
            "winner_id": s.entries[0].id,
            "games": [],
        }
    ]
    first.status = "completed"
    analysis = analyze_paths(s)
    bronze = next(m for m in s.matches if m.kind == "placement")
    assert all(bronze.id not in path for path in analysis.paths[person])


def test_solver_and_independent_verifier_allow_same_time_final_and_bronze():
    s = bracket()
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 2}
    s.assignments = [
        assign(s, 0, "08:00", court=0),
        assign(s, 1, "08:00", court=1),
        assign(s, 2, "09:30", court=0),
        assign(s, 3, "09:30", court=1),
    ]
    assert not validate_schedule(s)
    original = s.assignments.copy()
    result = solve_schedule(s)
    assert successful(result), result
    assert s.assignments == original
    assert result["diagnostics"]["candidate_conflict_strategy"] == "qualification_paths"
    assert not validate_schedule(s)


def test_cap_is_robust_for_all_paths_not_only_an_early_loser():
    s = bracket(8)
    s.config["player_workload"] = {"minimum_rest_minutes": 60, "max_matches_per_day": 2}
    before = content_hash(s)
    result = solve_schedule(s)
    assert result["status"] == "INFEASIBLE"
    assert any(i["code"] == "PLAYER_DAILY_CAPACITY_INSUFFICIENT" for i in result["issues"])
    assert content_hash(s) == before


def test_resource_limit_fails_explicitly_instead_of_truncating_paths():
    with pytest.raises(PathAnalysisIncomplete):
        analyze_paths(bracket(), state_limit=0)


def test_f_deferred_draw_never_places_one_person_in_two_slots_and_execution_shrinks_paths():
    from test_public_special_formats import execute_f, finish_pools
    from test_public_special_formats import fixture as special_fixture

    from squash_scheduler.domain.deferred_draws import bind_deferred_execution
    from squash_scheduler.domain.revisions import approve
    from squash_scheduler.domain.validation import target_basis

    s = special_fixture("f")
    analysis = analyze_paths(s)
    assert all(r["maximum_topology_matches"] <= 5 for r in analysis.reports.values())
    slot_matches = {
        m.id
        for m in s.matches
        if m.round == 1 and any(side.type == "draw_slot" for side in (m.side_a, m.side_b))
    }
    assert all(len(path & slot_matches) <= 1 for paths in analysis.paths.values() for path in paths)
    finish_pools(s)
    execution = execute_f(s)
    s, _ = approve(
        s,
        f"deferred_draw:{execution['id']}",
        target_basis(execution),
        "reviewer",
        "Synthetic execution",
    )
    bind_deferred_execution(s, execution["id"])
    executed = analyze_paths(s)
    assert sum(r["maximum_topology_matches"] == 2 for r in executed.reports.values()) == 3
    assert all(
        r["maximum_topology_matches"] <= analysis.reports[p]["maximum_topology_matches"]
        for p, r in executed.reports.items()
    )


def test_constructive_replay_uses_real_result_validation_and_preserves_source():
    from test_public_special_formats import fixture as special_fixture

    from squash_scheduler.scheduling.path_replay import replay_witness

    s = special_fixture("junior")
    before = content_hash(s)
    analysis = analyze_paths(s)
    person = s.participants[0].id
    witness = analysis.reports[person]["paths"][0]
    result = replay_witness(s, person, witness)
    assert result["verified"] and result["match_count"] == 3
    assert result["accepted_hypothetical_results"] == 8
    invalid = replay_witness(s, person, {**witness, "match_ids": []})
    assert not invalid["verified"]
    assert content_hash(s) == before


def test_draw_edge_must_extend_to_a_complete_permutation():
    from squash_scheduler.domain.deferred_draws import _Declaration
    from squash_scheduler.domain.models import EntrySide, canonical_hash
    from squash_scheduler.scheduling.paths import drawable_edges

    s = fixture()
    a, b, c = [EntrySide(entry_id=e.id) for e in s.entries[:3]]
    declaration = _Declaration.model_validate(
        {
            "division_id": s.divisions[0].id,
            "slots": [
                {"slot_id": "1", "candidate_sides": [a, b]},
                {"slot_id": "2", "candidate_sides": [a]},
                {"slot_id": "3", "candidate_sides": [b, c]},
            ],
            "procedure": {
                "description": "Synthetic fixed restrictions",
                "execution_trigger": "after_sources_confirmed",
                "duration_minutes": 0,
                "earliest_at": "2026-11-01T08:00:00+08:00",
            },
        }
    )
    assert drawable_edges(declaration) == {
        ("1", canonical_hash(b)),
        ("2", canonical_hash(a)),
        ("3", canonical_hash(c)),
    }
