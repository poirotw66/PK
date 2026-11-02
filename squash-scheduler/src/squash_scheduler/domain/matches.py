"""Generate deterministic played matches and derive graph/candidate semantics."""

from __future__ import annotations

from itertools import combinations
from uuid import UUID

from .draws import draw_inputs
from .formats import active_entries, format_basis, validate_formats
from .models import (
    ByeSide,
    DrawSlotSide,
    EntrySide,
    Issue,
    LoserSide,
    Match,
    MatchSide,
    Pool,
    PoolRankSide,
    Snapshot,
    Stage,
    WinnerSide,
    canonical_hash,
    stable_id,
)


def round_robin(entry_ids: list[str], division_id: str, stage_id: str) -> list[Match]:
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("Round robin entries must be unique")
    return [
        Match(
            id=stable_id("round-robin", stage_id, first, second),
            division_id=division_id,
            stage_id=stage_id,
            side_a=EntrySide(entry_id=first),
            side_b=EntrySide(entry_id=second),
            bracket_position=index,
        )
        for index, (first, second) in enumerate(combinations(sorted(entry_ids), 2))
    ]


def round_robin_counts(snapshot: Snapshot, stage_id: str) -> dict[str, dict[str, int]]:
    """Expected/played/scheduled/excluded appearances for review of incomplete pools."""
    stage = next(s for s in snapshot.stages if s.id == stage_id)
    groups = [p.entry_ids for p in snapshot.pools if p.stage_id == stage_id]
    groups = groups or [active_entries(snapshot, stage.division_id)]
    counts = {
        entry: {"expected": len(group) - 1, "scheduled": 0, "completed": 0, "excluded": 0}
        for group in groups
        for entry in group
    }
    assigned = {a.match_id for a in snapshot.assignments}
    for match in snapshot.matches:
        if match.stage_id != stage_id:
            continue
        for side in (match.side_a, match.side_b):
            if isinstance(side, EntrySide) and side.entry_id in counts:
                row = counts[side.entry_id]
                row["scheduled"] += int(
                    match.id in assigned and match.status not in {"excluded", "cancelled"}
                )
                row["completed"] += int(match.status == "completed")
                row["excluded"] += int(match.status == "excluded")
    return counts


def elimination(
    sides: list[MatchSide | str | None],
    division_id: str,
    stage_id: str,
    bronze: bool = False,
    bracket_capacity: int | None = None,
) -> list[Match]:
    """Explicit full capacity lists preserve positions; shorter lists get distributed byes.

    Distributed byes are a deterministic template convention, never historical evidence.
    The caller must approve the template/bye rule before production generation.
    """
    if len(sides) < 2:
        raise ValueError("Elimination requires at least two sides")
    capacity = bracket_capacity or 1 << (len(sides) - 1).bit_length()
    if capacity < len(sides) or capacity < 2 or capacity & (capacity - 1):
        raise ValueError("Bracket capacity must be a sufficient power of two")
    current = [
        EntrySide(entry_id=s)
        if isinstance(s, str)
        else (None if s is None or isinstance(s, ByeSide) else s)
        for s in sides
    ]
    if len(current) < capacity:
        # Insert a bye opposite distinct real sides before using any empty-only pairs.
        remaining = capacity - len(current)
        padded = []
        for side in current:
            padded.append(side)
            if remaining:
                padded.append(None)
                remaining -= 1
        current = padded + [None] * remaining
    real = [s for s in current if s is not None]
    keys = [canonical_hash(s) for s in real]
    if len(real) < 2 or len(keys) != len(set(keys)):
        raise ValueError("Elimination sides must be distinct and include two real qualifiers")
    matches = []
    semifinals = []
    round_number = 1
    while len(current) > 1:
        next_round = []
        for position in range(len(current) // 2):
            first, second = current[2 * position : 2 * position + 2]
            if first is None or second is None:
                next_round.append(first if first is not None else second)
                continue
            match = Match(
                id=stable_id("elimination", stage_id, str(round_number), str(position)),
                division_id=division_id,
                stage_id=stage_id,
                side_a=first,
                side_b=second,
                round=round_number,
                bracket_position=position,
            )
            matches.append(match)
            next_round.append(WinnerSide(match_id=match.id))
            if len(current) == 4:
                semifinals.append(match)
        current = next_round
        round_number += 1
    if bronze:
        if len(semifinals) != 2:
            raise ValueError("Bronze requires two played semifinals")
        matches.append(
            Match(
                id=stable_id("bronze", stage_id),
                division_id=division_id,
                stage_id=stage_id,
                kind="placement",
                round=round_number - 1,
                bracket_position=1,
                side_a=LoserSide(match_id=semifinals[0].id),
                side_b=LoserSide(match_id=semifinals[1].id),
                display_code="BRONZE",
            )
        )
    return matches


def generate_matches(snapshot: Snapshot) -> Snapshot:
    from .encouragement_draws import is_encouragement_definition

    if any(
        is_encouragement_definition(item)
        for division in snapshot.divisions
        for item in (division.format_plan or {}).get("placement_matches", [])
    ):
        raise ValueError(
            "ENCOURAGEMENT_DRAW_UNAPPROVED: use a qualified, confirmed encouragement draw"
        )
    issues = validate_formats(snapshot)
    if any(i.severity == "error" for i in issues):
        raise ValueError("; ".join(i.message for i in issues))
    generated, stages, pools = [], [], []
    old = {m.id: m for m in snapshot.matches}
    processed_divisions = set()
    for division in snapshot.divisions:
        entries = active_entries(snapshot, division.id)
        if not entries or division.status in {"cancelled", "merged", "exhibition_only"}:
            continue
        plan = division.format_plan or {}
        template = plan["template_id"]
        processed_divisions.add(division.id)
        if template == "round_robin":
            stage = Stage(
                id=stable_id("stage", division.id, "round_robin"),
                division_id=division.id,
                type="round_robin",
            )
            stages.append(stage)
            generated.extend(round_robin(entries, division.id, stage.id))
            continue
        runs = [
            r
            for r in snapshot.draws
            if r.get("division_id") == division.id
            and r.get("kind") not in {"deferred_slots", "encouragement"}
        ]
        run = runs[-1] if runs else None
        if (
            run is None
            or run.get("status") != "approved"
            or not run.get("approval_id")
            or not run.get("output")
            or run.get("format_basis_hash") != format_basis(plan)
        ):
            raise ValueError(
                "DRAW_APPROVAL_REQUIRED: an approved draw for the current format is required"
            )
        if run.get("input_hash") != canonical_hash(
            draw_inputs(snapshot, division.id, run["random_seed"])
        ):
            raise ValueError("APPROVAL_STALE: draw inputs have changed")
        output = run["output"]
        knockout = Stage(
            id=stable_id("stage", division.id, "elimination"),
            division_id=division.id,
            type="elimination",
            order=1,
        )
        if template == "single_elimination":
            sides = output["bracket_entry_ids"]
            if sorted(x for x in sides if x is not None) != entries:
                raise ValueError("Draw must contain each active entry exactly once")
        else:
            prelim = Stage(
                id=stable_id("stage", division.id, "pools"),
                division_id=division.id,
                type="round_robin",
            )
            stages.append(prelim)
            members = [entry for p in output["pools"] for entry in p["entry_ids"]]
            if (
                sorted(members) != entries
                or [len(p["entry_ids"]) for p in output["pools"]] != plan["pool_sizes"]
            ):
                raise ValueError("Draw pools must partition all active entries with approved sizes")
            sides = []
            for index, group in enumerate(output["pools"]):
                pool = Pool(
                    id=stable_id("pool", prelim.id, str(index)),
                    stage_id=prelim.id,
                    label=group["label"],
                    entry_ids=group["entry_ids"],
                    advancement_count=plan["advancement_counts"][index],
                )
                pools.append(pool)
                # Pool ID salts RR IDs so independent pools never share a match identity.
                pool_matches = round_robin(pool.entry_ids, division.id, prelim.id)
                generated.extend(pool_matches)
                sides.extend(
                    PoolRankSide(pool_id=pool.id, rank=rank)
                    for rank in range(1, pool.advancement_count + 1)
                )
            qualifier_positions = plan.get("qualifier_positions")
            if qualifier_positions is not None:
                # Each position refers to a zero-based qualifier index or None for an approved bye.
                indices = [i for i in qualifier_positions if i is not None]
                if len(qualifier_positions) != plan["bracket_capacity"] or sorted(indices) != list(
                    range(len(sides))
                ):
                    raise ValueError("qualifier_positions must contain each qualifier once")
                sides = [sides[i] if i is not None else None for i in qualifier_positions]
            elif template != "pools_3x3_top2_knockout6_bronze":
                if len(sides) != plan["bracket_capacity"]:
                    raise ValueError(
                        "Explicit qualifier_positions including bye positions are required"
                    )
        stages.append(knockout)
        generated.extend(
            elimination(
                sides,
                division.id,
                knockout.id,
                bronze="bronze" in plan.get("placement_matches", []),
                bracket_capacity=plan["bracket_capacity"],
            )
        )
    explicit_ids = set()
    main_matches = {m.id: m for m in generated if m.kind == "competitive"}
    explicit_definitions = {
        str(UUID(item["id"])): division.id
        for division in snapshot.divisions
        if division.id in processed_divisions
        for item in (division.format_plan or {}).get("placement_matches", [])
        if isinstance(item, dict)
    }
    if len(explicit_definitions) != sum(
        isinstance(item, dict)
        for division in snapshot.divisions
        if division.id in processed_divisions
        for item in (division.format_plan or {}).get("placement_matches", [])
    ):
        raise ValueError("PLACEMENT_ID_CONFLICT: placement IDs must be globally unique")
    main_pools = {p.id: p for p in pools}
    main_stages = {s.id: s for s in stages}
    for division in snapshot.divisions:
        if division.id not in processed_divisions:
            continue
        definitions = [
            item
            for item in (division.format_plan or {}).get("placement_matches", [])
            if isinstance(item, dict)
        ]
        if not definitions:
            continue
        keys = sorted({d["stage_key"] for d in definitions if "stage_key" in d})
        placement_stages = {}
        for key in ([None] if any("stage_key" not in d for d in definitions) else []) + keys:
            label = next(
                (
                    d["stage_label"]
                    for d in definitions
                    if d.get("stage_key") == key and "stage_label" in d
                ),
                None,
            )
            stage = Stage(
                id=(
                    stable_id("stage", division.id, "placement")
                    if key is None
                    else stable_id("placement-stage", division.id, key)
                ),
                division_id=division.id,
                type="placement",
                order=2 if key is None else 3 + keys.index(key),
                label=label,
                stage_key=key,
            )
            stages.append(stage)
            placement_stages[key] = stage
        for definition in definitions:
            placement_stage = placement_stages[definition.get("stage_key")]
            if str(UUID(definition["id"])) in {m.id for m in generated}:
                raise ValueError(
                    "PLACEMENT_ID_CONFLICT: placement IDs must not collide with generated matches"
                )
            placement = Match.model_validate(
                {
                    "id": definition["id"],
                    "division_id": division.id,
                    "stage_id": placement_stage.id,
                    "kind": "placement",
                    "side_a": definition["side_a"],
                    "side_b": definition["side_b"],
                    "round": definition["round"],
                    "bracket_position": definition["bracket_position"],
                    "display_code": definition.get("code", definition["label"]),
                }
            )
            for side in (placement.side_a, placement.side_b):
                if isinstance(side, (WinnerSide, LoserSide)):
                    source = main_matches.get(side.match_id)
                    valid = (
                        (source is not None and source.division_id == division.id)
                        or explicit_definitions.get(side.match_id) == division.id
                    ) and side.match_id != placement.id
                elif isinstance(side, EntrySide):
                    valid = side.entry_id in active_entries(snapshot, division.id)
                elif isinstance(side, PoolRankSide):
                    referenced_pool = main_pools.get(side.pool_id)
                    valid = bool(
                        referenced_pool
                        and side.rank <= referenced_pool.advancement_count
                        and main_stages[referenced_pool.stage_id].division_id == division.id
                    )
                elif isinstance(side, DrawSlotSide):
                    draw = next((d for d in snapshot.draws if d["id"] == side.draw_run_id), None)
                    valid = bool(
                        draw
                        and draw.get("division_id") == division.id
                        and draw.get("status") == "approved"
                        and any(
                            str(slot["slot_id"]) == side.slot_id
                            and slot.get("entry_id") in active_entries(snapshot, division.id)
                            for slot in (draw.get("output") or {}).get("slots", [])
                        )
                    )
                else:
                    valid = False
                if not valid:
                    raise ValueError(
                        "PLACEMENT_REFERENCE_INVALID: placement sides must resolve within the generated division graph"
                    )
            generated.append(placement)
            explicit_ids.add(placement.id)
    from .encouragement_draws import generated_draws

    encouragement_stages, encouragement_matches = generated_draws(snapshot)
    if {m.id for m in generated} & {m.id for m in encouragement_matches}:
        raise ValueError(
            "ENCOURAGEMENT_GRAPH_INVALID: authored and reviewed encouragement draws conflict"
        )
    stages.extend(encouragement_stages)
    generated.extend(encouragement_matches)
    explicit_ids.update(m.id for m in encouragement_matches)
    # Check authored topology before ancestry fingerprints and before mutating the snapshot.
    placement_graph = {
        match.id: {
            side.match_id
            for side in (match.side_a, match.side_b)
            if isinstance(side, (WinnerSide, LoserSide)) and side.match_id in explicit_ids
        }
        for match in generated
        if match.id in explicit_ids
    }
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit_placement(match_id: str) -> None:
        if match_id in visiting:
            raise ValueError("PLACEMENT_REFERENCE_INVALID: placement dependencies contain a cycle")
        if match_id in visited:
            return
        visiting.add(match_id)
        for dependency in placement_graph[match_id]:
            visit_placement(dependency)
        visiting.remove(match_id)
        visited.add(match_id)

    for match_id in placement_graph:
        visit_placement(match_id)
    exclusions = {
        e["match_id"]
        for e in snapshot.exclusions
        if e.get("approval_id") and e.get("reason") and e.get("status", "approved") == "approved"
    }
    for match in generated:
        if match.id in old:
            if (
                old[match.id].kind in {"placement", "exhibition"}
                and old[match.id].kind != match.kind
            ):
                raise ValueError(
                    "MATCH_GENERATION_CONFLICT: generated ID collides with a manual match"
                )
            match.status = old[match.id].status
            match.rule_snapshot = old[match.id].rule_snapshot
            # An explicitly bound reviewed future draw is part of this bracket's
            # authored qualification topology, and survives deterministic refresh.
            for field in ("side_a", "side_b"):
                previous_side = getattr(old[match.id], field)
                if isinstance(previous_side, DrawSlotSide) and any(
                    d.get("id") == previous_side.draw_run_id and d.get("kind") == "deferred_slots"
                    for d in snapshot.draws
                ):
                    setattr(match, field, previous_side.model_copy(deep=True))
            if match.id not in explicit_ids:
                match.display_code = old[match.id].display_code or match.display_code
        if match.id in exclusions:
            match.status = "excluded"
    # Generated bronze IDs are known; other placement/exhibition matches are authored manually.
    generated_ids = {m.id for m in generated}
    keep = [
        m
        for m in snapshot.matches
        if m.id not in generated_ids
        and (
            m.division_id not in processed_divisions
            or m.kind == "exhibition"
            or (
                m.kind == "placement"
                and m.id != stable_id("bronze", m.stage_id)
                and m.stage_id != stable_id("stage", m.division_id, "placement")
                and not any(
                    s.id == m.stage_id and s.type == "placement" and s.stage_key is not None
                    for s in snapshot.stages
                )
            )
        )
    ]
    # Build a separate graph so a rejected redraw cannot partially replace the original graph.
    candidate = snapshot.model_copy(deep=True)
    candidate.matches = keep + generated
    retained_stage_ids = {m.stage_id for m in keep} | {
        s.id for s in snapshot.stages if s.division_id not in processed_divisions
    }
    referenced_pool_ids = {
        side.pool_id
        for m in keep
        for side in (m.side_a, m.side_b)
        if isinstance(side, PoolRankSide)
    }
    retained_stage_ids.update(p.stage_id for p in snapshot.pools if p.id in referenced_pool_ids)
    new_stage_ids = {s.id for s in stages}
    candidate.stages = stages + [
        s
        for s in snapshot.stages
        if s.id not in new_stage_ids
        and (s.division_id not in processed_divisions or s.id in retained_stage_ids)
    ]
    old_stages = {s.id: s for s in snapshot.stages}
    for stage in candidate.stages:
        if stage.id in old_stages:
            stage.rule_set_id = old_stages[stage.id].rule_set_id
    new_pool_ids = {p.id for p in pools}
    candidate.pools = pools + [
        p
        for p in snapshot.pools
        if p.id not in new_pool_ids
        and (p.stage_id in retained_stage_ids or p.id in referenced_pool_ids)
    ]
    from .rules import refresh_rule_snapshots

    refresh_rule_snapshots(candidate)
    protected_ids = {m.id for m in snapshot.matches if m.status in {"in_progress", "completed"}}
    protected_ids.update(
        a.match_id for a in snapshot.assignments if a.locks.get("sides") or a.locks.get("draw")
    )
    old_people, new_people = possible_participants(snapshot), possible_participants(candidate)
    old_qualifications = _qualification_fingerprints(snapshot)
    new_qualifications = _qualification_fingerprints(candidate)
    new_matches = {m.id: m for m in candidate.matches}
    for match_id in protected_ids:
        previous, replacement = old.get(match_id), new_matches.get(match_id)
        if (
            previous is None
            or replacement is None
            or previous.status != replacement.status
            or old_qualifications.get(match_id) != new_qualifications.get(match_id)
            or old_people.get(match_id) != new_people.get(match_id)
        ):
            raise ValueError(
                "MATCH_REGENERATION_LOCKED: started or locked match qualifications would change"
            )
    errors = [i for i in validate_graph(candidate) if i.severity == "error"]
    if errors:
        raise ValueError("; ".join(i.message for i in errors))
    snapshot.matches = candidate.matches
    snapshot.stages = candidate.stages
    snapshot.pools = candidate.pools
    return snapshot


def _qualification_fingerprints(snapshot: Snapshot) -> dict[str, str]:
    """Include each qualification's ancestry, even when the total candidate union is unchanged."""
    matches = {m.id: m for m in snapshot.matches}
    pools = {p.id: p for p in snapshot.pools}
    draws = {d["id"]: d for d in snapshot.draws}
    people = _entry_people(snapshot)
    memo: dict[str, str] = {}

    def side_basis(side: MatchSide, visiting: set[str]) -> dict:
        basis = side.model_dump(mode="json")
        if isinstance(side, EntrySide):
            basis["participant_id"] = people.get(side.entry_id)
        elif isinstance(side, PoolRankSide):
            pool = pools.get(side.pool_id)
            basis["pool"] = (
                None
                if pool is None
                else {
                    "stage_id": pool.stage_id,
                    "entry_ids": sorted(pool.entry_ids),
                    "advancement_count": pool.advancement_count,
                }
            )
        elif isinstance(side, (WinnerSide, LoserSide)):
            basis["source"] = resolve(side.match_id, visiting)
        elif isinstance(side, DrawSlotSide):
            slots = (draws.get(side.draw_run_id, {}).get("output") or {}).get("slots", [])
            basis["resolved_entry_id"] = next(
                (
                    slot.get("entry_id")
                    for slot in slots
                    if str(slot.get("slot_id")) == side.slot_id
                ),
                None,
            )
        return basis

    def resolve(match_id: str, visiting: set[str]) -> str:
        if match_id in memo:
            return memo[match_id]
        if match_id in visiting or match_id not in matches:
            return canonical_hash({"unresolved_match_id": match_id})
        match = matches[match_id]
        memo[match_id] = canonical_hash(
            {
                "division_id": match.division_id,
                "stage_id": match.stage_id,
                "kind": match.kind,
                "round": match.round,
                "bracket_position": match.bracket_position,
                "excluded": match.status in {"cancelled", "excluded"},
                "side_a": side_basis(match.side_a, visiting | {match_id}),
                "side_b": side_basis(match.side_b, visiting | {match_id}),
            }
        )
        return memo[match_id]

    return {match_id: resolve(match_id, set()) for match_id in matches}


def dependency_map(snapshot: Snapshot) -> dict[str, set[str]]:
    pools = {p.id: p for p in snapshot.pools}
    result = {}
    for match in snapshot.matches:
        dependencies = set()
        for side in (match.side_a, match.side_b):
            if isinstance(side, (WinnerSide, LoserSide)):
                dependencies.add(side.match_id)
            elif isinstance(side, PoolRankSide) and side.pool_id in pools:
                pool = pools[side.pool_id]
                members = set(pool.entry_ids)
                dependencies.update(
                    m.id
                    for m in snapshot.matches
                    if m.stage_id == pool.stage_id
                    and m.status not in {"excluded", "cancelled"}
                    and isinstance(m.side_a, EntrySide)
                    and isinstance(m.side_b, EntrySide)
                    and {m.side_a.entry_id, m.side_b.entry_id} <= members
                )
            elif isinstance(side, DrawSlotSide):
                draw = next((d for d in snapshot.draws if d.get("id") == side.draw_run_id), {})
                if draw.get("kind") == "deferred_slots":
                    from .deferred_draws import slot_dependencies

                    try:
                        dependencies.update(slot_dependencies(snapshot, side))
                    except (ValueError, KeyError, TypeError, RecursionError):
                        pass  # Invalid records are reported by validate_graph.
        result[match.id] = dependencies
    return result


def _entry_people(snapshot: Snapshot) -> dict[str, str]:
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    return {
        e.id: registrations[e.registration_id]
        for e in snapshot.entries
        if e.registration_id in registrations
    }


def possible_participants(snapshot: Snapshot) -> dict[str, set[str]]:
    people = _entry_people(snapshot)
    pools = {p.id: p for p in snapshot.pools}
    draws = {r["id"]: r for r in snapshot.draws}
    matches = {m.id: m for m in snapshot.matches}
    memo: dict[str, set[str]] = {}

    def side_people(side: MatchSide, visiting: set[str]) -> set[str]:
        if isinstance(side, EntrySide):
            return {people[side.entry_id]} if side.entry_id in people else set()
        if isinstance(side, PoolRankSide):
            pool = pools.get(side.pool_id)
            return {people[e] for e in pool.entry_ids if e in people} if pool else set()
        if isinstance(side, (WinnerSide, LoserSide)):
            return resolve(side.match_id, visiting)
        if isinstance(side, DrawSlotSide):
            record = draws.get(side.draw_run_id, {})
            if record.get("kind") == "deferred_slots":
                from .deferred_draws import slot_candidates

                try:
                    return slot_candidates(snapshot, side)
                except (ValueError, KeyError, TypeError, RecursionError):
                    return set()  # Invalid records cannot enter a scheduling model.
            slots = (record.get("output") or {}).get("slots", [])
            entry = next(
                (s.get("entry_id") for s in slots if str(s["slot_id"]) == side.slot_id), None
            )
            return {people[entry]} if entry in people else set()
        return set()

    def resolve(match_id: str, visiting: set[str]) -> set[str]:
        if match_id in memo:
            return memo[match_id]
        if match_id in visiting or match_id not in matches:
            return set()
        match = matches[match_id]
        value = side_people(match.side_a, visiting | {match_id}) | side_people(
            match.side_b, visiting | {match_id}
        )
        memo[match_id] = value
        return value

    return {m.id: resolve(m.id, set()) for m in snapshot.matches}


def validate_graph(snapshot: Snapshot) -> list[Issue]:
    from .validation import reviewed_entry_change

    entry_review = reviewed_entry_change(snapshot)
    from .encouragement_draws import validate_encouragement

    issues = validate_encouragement(snapshot)
    if entry_review is None and any(
        c.get("kind") == "entry_change" and c.get("before_snapshot", {}).get("matches")
        for c in snapshot.changes
    ):
        issues.append(
            Issue(
                code="ENTRY_CHANGE_REVIEW_REQUIRED",
                gate="G3",
                entity_type="entry_change",
                message="Preserved competition history requires current entry-change review",
            )
        )
    matches = {m.id: m for m in snapshot.matches}
    stages = {s.id: s for s in snapshot.stages}
    entries = {e.id: e for e in snapshot.entries}
    pools = {p.id: p for p in snapshot.pools}
    draws = {d["id"]: d for d in snapshot.draws}
    people = _entry_people(snapshot)

    def report(match: Match, message: str, code: str = "MATCH_REFERENCE_INVALID") -> None:
        issues.append(
            Issue(code=code, gate="G3", entity_type="match", entity_id=match.id, message=message)
        )

    def direct_person(side: MatchSide) -> str | None:
        if isinstance(side, EntrySide):
            return people.get(side.entry_id)
        if isinstance(side, DrawSlotSide):
            slots = (draws.get(side.draw_run_id, {}).get("output") or {}).get("slots", [])
            entry = next(
                (x.get("entry_id") for x in slots if str(x.get("slot_id")) == side.slot_id), None
            )
            return people.get(entry) if isinstance(entry, str) else None
        return None

    for match in snapshot.matches:
        if match.stage_id not in stages or stages[match.stage_id].division_id != match.division_id:
            report(match, "Match stage is missing or belongs to another division.")
        for side in (match.side_a, match.side_b):
            if isinstance(side, EntrySide):
                entry = entries.get(side.entry_id)
                if not entry or entry.effective_division_id != match.division_id:
                    report(match, "Entry is missing or belongs to another division.")
            elif isinstance(side, PoolRankSide):
                pool = pools.get(side.pool_id)
                if (
                    not pool
                    or side.rank > pool.advancement_count
                    or pool.stage_id not in stages
                    or stages[pool.stage_id].division_id != match.division_id
                ):
                    report(match, "Pool rank qualification is invalid.")
            elif isinstance(side, (WinnerSide, LoserSide)):
                source = matches.get(side.match_id)
                if not source or source.division_id != match.division_id:
                    report(match, "Source match is missing or belongs to another division.")
                elif source.status in {"excluded", "cancelled"}:
                    report(match, "Advancement cannot depend on a cancelled/excluded match.")
            elif isinstance(side, DrawSlotSide):
                draw = draws.get(side.draw_run_id)
                if draw and draw.get("kind") == "deferred_slots":
                    from .deferred_draws import slot_candidates

                    try:
                        if draw.get("division_id") != match.division_id:
                            raise ValueError("Deferred draw belongs to another division")
                        slot_candidates(snapshot, side)
                    except (ValueError, KeyError, TypeError, RecursionError) as exc:
                        report(match, str(exc), "DEFERRED_DRAW_INVALID")
                    continue
                slots = (draw.get("output") or {}).get("slots", []) if draw else []
                slot = next((s for s in slots if str(s.get("slot_id")) == side.slot_id), None)
                if (
                    not draw
                    or draw.get("division_id") != match.division_id
                    or not slot
                    or slot.get("entry_id") not in entries
                ):
                    report(match, "Draw slot does not resolve to an entry in this division.")
            else:
                report(match, "A bye must auto advance without a played match.")
        first, second = direct_person(match.side_a), direct_person(match.side_b)
        if (first is not None and first == second) or match.side_a == match.side_b:
            report(match, "A participant or qualification cannot play itself.", "MATCH_SELF_PLAY")
    graph = dependency_map(snapshot)
    visiting, visited = set(), set()

    def visit(match_id: str) -> None:
        if match_id in visiting:
            report(matches[match_id], "Match dependencies contain a cycle.", "MATCH_GRAPH_CYCLE")
            return
        if match_id in visited:
            return
        visiting.add(match_id)
        for dependency in graph[match_id]:
            if dependency in graph:
                visit(dependency)
        visiting.remove(match_id)
        visited.add(match_id)

    for match_id in graph:
        visit(match_id)
    # Completeness is checked against all expected unordered entry pairs, exclusions retained.
    for stage in snapshot.stages:
        if stage.type != "round_robin":
            continue
        stage_pools = [p for p in snapshot.pools if p.stage_id == stage.id]
        groups = [p.entry_ids for p in stage_pools] or [active_entries(snapshot, stage.division_id)]
        if entry_review and not stage_pools:
            # The reviewed transition proves these preserved pairings. Withdrawn
            # members remain historical opponents; replacements keep their positions.
            groups = [
                sorted(
                    {
                        side.entry_id
                        for match in snapshot.matches
                        if match.stage_id == stage.id
                        for side in (match.side_a, match.side_b)
                        if isinstance(side, EntrySide)
                    }
                )
            ]
        expected = {tuple(sorted(pair)) for group in groups for pair in combinations(group, 2)}
        actual: dict[tuple[str, ...], list[Match]] = {}
        for match in snapshot.matches:
            if (
                match.stage_id == stage.id
                and isinstance(match.side_a, EntrySide)
                and isinstance(match.side_b, EntrySide)
            ):
                key = tuple(sorted((match.side_a.entry_id, match.side_b.entry_id)))
                actual.setdefault(key, []).append(match)
                if key not in expected:
                    report(match, "Round robin pair crosses pools or contains an unexpected entry.")
                if match.status == "cancelled":
                    report(
                        match,
                        "A cancelled round robin match requires an approved exclusion.",
                        "MATCH_PAIR_MISSING",
                    )
        for key in expected - actual.keys():
            issues.append(
                Issue(
                    code="MATCH_PAIR_MISSING",
                    gate="G3",
                    entity_type="stage",
                    entity_id=stage.id,
                    message=f"Expected round robin pair is missing: {key}",
                )
            )
        for group_matches in actual.values():
            if len(group_matches) > 1:
                report(group_matches[0], "Round robin pair is duplicated.", "MATCH_PAIR_DUPLICATE")
        for match in (m for values in actual.values() for m in values if m.status == "excluded"):
            exclusion = next(
                (
                    e
                    for e in snapshot.exclusions
                    if e.get("match_id") == match.id and e.get("approval_id") and e.get("reason")
                ),
                None,
            )
            if exclusion is None:
                report(
                    match, "Exclusion requires a reason and approval.", "MATCH_EXCLUSION_UNAPPROVED"
                )
            else:
                issues.append(
                    Issue(
                        code="ROUND_ROBIN_INCOMPLETE",
                        severity="warning",
                        gate="G3",
                        entity_type="match",
                        entity_id=match.id,
                        message="Approved exclusion changes per-player match counts; ranking needs review.",
                    )
                )
    return issues
