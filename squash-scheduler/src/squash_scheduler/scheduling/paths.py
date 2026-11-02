"""Per-person qualification paths, preserving shared outcome/rank/draw decisions.

These are robust topology scenarios, never an optimizer choosing who wins.
Unresolved pool ranks remain possible; score-level feasibility is not inferred.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from itertools import combinations
from typing import Any

from squash_scheduler.domain.matches import possible_participants
from squash_scheduler.domain.models import Snapshot, canonical_hash
from squash_scheduler.domain.rankings import _current_results, resolve_side

Term = frozenset[tuple[str, str]]
Formula = tuple[Term, ...]


class PathAnalysisIncomplete(ValueError):
    """An unsupported topology or resource bound must never become a safe schedule."""


def normalize(terms) -> Formula:
    unique = sorted(set(terms), key=lambda term: (len(term), sorted(term)))
    kept: list[Term] = []
    for term in unique:
        if not any(prior <= term for prior in kept):
            kept.append(term)
    if len(kept) > 4096:
        raise PathAnalysisIncomplete("Qualification formula exceeds 4096 terms")
    return tuple(kept)


def require(formula: Formula, key: str, value: str) -> Formula:
    return normalize(
        term | {(key, value)} for term in formula if all(k != key or v == value for k, v in term)
    )


def drawable_edges(declaration) -> set[tuple[str, str]]:
    """Keep candidate-slot edges only when a complete legal permutation exists."""
    restrictions = {
        canonical_hash(c.candidate_side): set(c.slot_ids)
        for c in declaration.allowed_positions.constraints
    }
    choices = {
        slot.slot_id: {
            canonical_hash(c)
            for c in slot.candidate_sides
            if canonical_hash(c) not in restrictions
            or slot.slot_id in restrictions[canonical_hash(c)]
        }
        for slot in declaration.slots
    }
    allowed = set()
    for fixed_slot, sources in choices.items():
        for fixed_source in sources:
            owners = {fixed_source: fixed_slot}

            def assign(slot, visited, fixed_source=fixed_source, owners=owners):
                for source in sorted(choices[slot]):
                    if source == fixed_source or source in visited:
                        continue
                    visited.add(source)
                    if source not in owners or assign(owners[source], visited):
                        owners[source] = slot
                        return True
                return False

            if all(assign(slot, set()) for slot in choices if slot != fixed_slot):
                allowed.add((fixed_slot, fixed_source))
    return allowed


@dataclass
class PathAnalysis:
    paths: dict[str, list[frozenset[str]]]
    reports: dict[str, dict[str, Any]]
    coexist: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def shared(self, first: str, second: str) -> set[str]:
        return self.coexist.get((first, second) if first < second else (second, first), set())


def analyze_paths(
    snapshot: Snapshot, match_ids: set[str] | None = None, *, state_limit: int = 50000
) -> PathAnalysis:
    matches = {m.id: m for m in snapshot.matches}
    active = (
        match_ids
        if match_ids is not None
        else {
            m.id
            for m in snapshot.matches
            if m.status not in {"cancelled", "excluded"}
            and m.side_a.type != "bye"
            and m.side_b.type != "bye"
        }
    )
    candidates = possible_participants(snapshot)
    people = sorted(p.id for p in snapshot.participants)
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    owners = {e.id: registrations[e.registration_id] for e in snapshot.entries}
    entries = {e.id: e for e in snapshot.entries}
    pools = {p.id: p for p in snapshot.pools}
    draws = {d["id"]: d for d in snapshot.draws}
    names = {p.id: p.display_name for p in snapshot.participants}
    results = _current_results(snapshot)
    resolved: dict[str, str | None] = {}
    draw_edges: dict[str, set[tuple[str, str]]] = {}
    paths_by_person, reports = {}, {}
    for person in people:
        domains: dict[str, set[str]] = {}
        memo: dict[str, Formula] = {}
        visiting: set[str] = set()

        def side_formula(
            side, person=person, memo=memo, visiting=visiting, domains=domains
        ) -> Formula:
            key = side.model_dump_json()
            if key in memo:
                return memo[key]
            if key in visiting:
                raise PathAnalysisIncomplete("Qualification path contains a cycle")
            visiting.add(key)
            if key not in resolved:
                resolved[key] = resolve_side(snapshot, side)
            entry = resolved[key]
            formula: Formula
            if entry is not None:
                formula = (frozenset(),) if owners.get(entry) == person else ()
            elif side.type == "entry":
                formula = (frozenset(),) if owners.get(side.entry_id) == person else ()
            elif side.type == "bye":
                formula = ()
            elif side.type == "pool_rank":
                pool = pools[side.pool_id]
                own = [
                    eid
                    for eid in pool.entry_ids
                    if owners.get(eid) == person and entries[eid].status == "active"
                ]
                if len(own) > 1:
                    raise PathAnalysisIncomplete(
                        "One participant has multiple entries in the same pool"
                    )
                variable = f"pool_rank:{pool.id}"
                domains[variable] = {str(i) for i in range(1, len(pool.entry_ids) + 1)}
                formula = (
                    (frozenset({(variable, str(side.rank))}),)
                    if own and side.rank <= pool.advancement_count
                    else ()
                )
            elif side.type in {"match_winner", "match_loser"}:
                source = matches[side.match_id]
                record = results.get(source.id)
                if record and record.get("outcome") in {"double_forfeit", "cancelled"}:
                    formula = ()
                elif record and record.get("winner_id") in owners:
                    winner_person = owners[record["winner_id"]]
                    if side.type == "match_winner":
                        formula = (frozenset(),) if winner_person == person else ()
                    else:
                        formula = (
                            ()
                            if winner_person == person
                            else normalize(
                                [*side_formula(source.side_a), *side_formula(source.side_b)]
                            )
                        )
                else:
                    variable = f"winner_side:{source.id}"
                    domains[variable] = {"a", "b"}
                    winner = side.type == "match_winner"
                    formula = normalize(
                        [
                            *require(side_formula(source.side_a), variable, "a" if winner else "b"),
                            *require(side_formula(source.side_b), variable, "b" if winner else "a"),
                        ]
                    )
            elif side.type == "draw_slot":
                draw = draws[side.draw_run_id]
                if draw.get("kind") != "deferred_slots":
                    raise PathAnalysisIncomplete("Unresolved fixed draw cannot be analyzed")
                from squash_scheduler.domain.deferred_draws import _declaration

                declaration = _declaration(draw)
                if draw["id"] not in draw_edges:
                    draw_edges[draw["id"]] = drawable_edges(declaration)
                slot = next(s for s in declaration.slots if s.slot_id == side.slot_id)
                variable = f"draw_slot:{draw['id']}"
                domains[variable] = {s.slot_id for s in declaration.slots} | {"__not_qualified__"}
                allowed = {
                    canonical_hash(c.candidate_side): set(c.slot_ids)
                    for c in declaration.allowed_positions.constraints
                }
                terms: list[Term] = []
                for candidate in slot.candidate_sides:
                    if (side.slot_id, canonical_hash(candidate)) not in draw_edges[draw["id"]]:
                        continue
                    permitted = allowed.get(canonical_hash(candidate))
                    if permitted is None or side.slot_id in permitted:
                        terms.extend(require(side_formula(candidate), variable, side.slot_id))
                formula = normalize(terms)
            else:
                raise PathAnalysisIncomplete(f"Unsupported qualification side: {side.type}")
            visiting.remove(key)
            memo[key] = formula
            return formula

        formulas = []
        for mid in sorted(active):
            if person not in candidates.get(mid, set()):
                continue
            m = matches[mid]
            formula = normalize([*side_formula(m.side_a), *side_formula(m.side_b)])
            if formula:
                formulas.append((mid, formula))
        states = 0

        @cache
        def expand(state, person=person, domains=domains):
            nonlocal states
            states += 1
            if states > state_limit:
                raise PathAnalysisIncomplete(f"Path state budget exceeded for {person}")
            certain = frozenset(mid for mid, formula in state if frozenset() in formula)
            remaining = tuple(
                (mid, formula) for mid, formula in state if formula and frozenset() not in formula
            )
            if not remaining:
                return ((certain, ()),)
            frequencies = Counter(
                k for _, formula in remaining for term in formula for k, _ in term
            )
            variable = min(frequencies, key=lambda k: (-frequencies[k], k))
            found: dict[frozenset[str], tuple[tuple[str, str], ...]] = {}
            for value in sorted(domains[variable]):
                reduced = tuple(
                    (
                        mid,
                        normalize(
                            term - {(variable, value)}
                            for term in formula
                            if all(k != variable or v == value for k, v in term)
                        ),
                    )
                    for mid, formula in remaining
                )
                for path, witness in expand(reduced):
                    found.setdefault(certain | path, ((variable, value), *witness))
                if len(found) > 4096:
                    raise PathAnalysisIncomplete(f"More than 4096 distinct paths for {person}")
            return tuple(found.items())

        all_paths = dict(expand(tuple(formulas)))
        # With nonnegative daily counts, subsets are dominated by feasible supersets.
        ordered = sorted(all_paths, key=lambda path: (-len(path), sorted(path)))
        maximal: list[frozenset[str]] = []
        for path in ordered:
            if not any(path <= prior for prior in maximal):
                maximal.append(path)
        paths_by_person[person] = maximal
        known = {mid for mid, formula in formulas if frozenset() in formula}
        reports[person] = {
            "participant_id": person,
            "name": names[person],
            "known_match_count": len(known),
            "raw_candidate_count": sum(person in candidates.get(mid, set()) for mid in active),
            "maximum_topology_matches": max(map(len, maximal), default=0),
            "distinct_paths": len(all_paths),
            "maximal_paths": len(maximal),
            "states": states,
            "paths": [
                {"match_ids": sorted(path), "decisions": dict(all_paths[path])} for path in maximal
            ],
            "interpretation": "完整資格拓樸路徑；尚未核定的池內名次保留可能性，未推定未來局分或指定勝者。",
        }
    coexist: dict[tuple[str, str], set[str]] = {}
    for person, paths in paths_by_person.items():
        for path in paths:
            for pair in combinations(sorted(path), 2):
                coexist.setdefault(pair, set()).add(person)
    return PathAnalysis(paths_by_person, reports, coexist)
