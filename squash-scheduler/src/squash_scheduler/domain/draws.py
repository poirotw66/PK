"""Bounded reproducible draws. Saved outputs, not seeds alone, are replay authority.

Output slots use one-based string IDs. Pools include explicit member IDs; elimination
outputs contain bracket_entry_ids with None at approved bye positions.
"""

from __future__ import annotations

import platform
import random
from typing import Any

from .formats import active_entries, format_basis, validate_formats
from .models import Snapshot, canonical_hash, new_id, utcnow

ALGORITHM_VERSION = "fisher-yates-bounded-v1"


def draw_basis(record: dict[str, Any]) -> str:
    return canonical_hash({k: v for k, v in record.items() if k not in {"status", "approval_id"}})


def _shuffle(items: list[Any], rng: random.Random) -> list[Any]:
    result = list(items)
    for i in range(len(result) - 1, 0, -1):
        j = rng.randrange(i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def draw_inputs(snapshot: Snapshot, division_id: str, random_seed: int) -> dict[str, Any]:
    division = next(d for d in snapshot.divisions if d.id == division_id)
    rules = {**snapshot.config.get("draw_defaults", {}), **division.draw_overrides}
    registrations = {r.id: r for r in snapshot.registrations}
    participants = {p.id: p for p in snapshot.participants}
    affiliations = {}
    for entry in snapshot.entries:
        if entry.id in active_entries(snapshot, division_id):
            registration = registrations.get(entry.registration_id)
            person = participants.get(registration.participant_id) if registration else None
            affiliations[entry.id] = sorted(person.affiliation_ids) if person else []
    return {
        "division_id": division_id,
        "entry_ids": active_entries(snapshot, division_id),
        "format": {
            k: v
            for k, v in (division.format_plan or {}).items()
            if k not in {"status", "approval_id"}
        },
        "constraints": rules,
        "affiliations": affiliations,
        "random_seed": random_seed,
    }


def run_draw(
    snapshot: Snapshot, division_id: str, random_seed: int, actor_id: str, reason: str | None = None
) -> dict[str, Any]:
    if type(random_seed) is not int or not actor_id.strip():
        raise ValueError("An integer random_seed and actor_id are required")
    division = next((d for d in snapshot.divisions if d.id == division_id), None)
    if division is None:
        raise ValueError("Unknown division")
    errors = [i for i in validate_formats(snapshot) if i.entity_id == division_id]
    if errors:
        raise ValueError("; ".join(i.message for i in errors))
    prior = [
        d
        for d in snapshot.draws
        if d.get("division_id") == division_id
        and d.get("kind") not in {"deferred_slots", "encouragement"}
    ]
    if prior and not (reason and reason.strip()):
        raise ValueError("A redraw requires a reason and creates a new run")
    inputs = draw_inputs(snapshot, division_id, random_seed)
    rules = inputs["constraints"]
    mode = rules.get(
        "affiliation_avoidance_mode", rules.get("affiliation_avoidance", {}).get("mode")
    )
    scope = rules.get(
        "affiliation_avoidance_scope", rules.get("affiliation_avoidance", {}).get("scope")
    )
    if mode not in {"disabled", "hard", "soft"}:
        raise ValueError("DRAW_CONSTRAINT_UNRESOLVED: affiliation avoidance mode must be explicit")
    if mode != "disabled" and scope not in {"pool", "first_round"}:
        raise ValueError("DRAW_CONSTRAINT_UNRESOLVED: avoidance scope is required")
    seed_mode = rules.get("seeded_entries_mode", "none")
    if seed_mode not in {"none", "provided"}:
        raise ValueError("DRAW_CONSTRAINT_UNRESOLVED: seeded entry mode must be explicit")
    if rules.get("special_slot_rules") not in (None, [], {}):
        raise ValueError(
            "DRAW_CONSTRAINT_UNRESOLVED: special rules require explicit permitted_slots"
        )
    plan = division.format_plan or {}
    entries = inputs["entry_ids"]
    is_pool = plan["template_id"].startswith("pools_")
    capacity = (
        len(entries)
        if is_pool or plan["template_id"] == "round_robin"
        else plan["bracket_capacity"]
    )
    slot_ids = [str(i + 1) for i in range(capacity)]
    bye_positions = []
    if capacity > len(entries):
        allocation = rules.get("bye_allocation_rule")
        if not isinstance(allocation, dict) or not isinstance(allocation.get("positions"), list):
            raise ValueError("DRAW_CONSTRAINT_UNRESOLVED: explicit bye positions are required")
        bye_positions = [str(x) for x in allocation["positions"]]
        if (
            len(bye_positions) != capacity - len(entries)
            or len(set(bye_positions)) != len(bye_positions)
            or not set(bye_positions) <= set(slot_ids)
        ):
            raise ValueError("Invalid bye positions")
    available = [p for p in slot_ids if p not in bye_positions]
    allowed = {e: set(available) for e in entries}
    for entry, positions in rules.get("permitted_slots", {}).items():
        if entry not in allowed:
            raise ValueError("Permitted slots reference unknown entry")
        positions = {str(p) for p in positions}
        if not positions or not positions <= set(available):
            raise ValueError("Permitted position does not exist or is a bye")
        allowed[entry] &= positions
    seeded = rules.get("seeded_entries", [])
    if seed_mode == "none" and seeded:
        raise ValueError("seeded_entries contradicts seeded_entries_mode none")
    seed_numbers = set()
    seeded_ids = set()
    for seed in seeded:
        entry = seed.get("entry_id")
        number = seed.get("seed_number")
        positions = {str(p) for p in seed.get("allowed_positions", [])}
        if (
            entry not in allowed
            or entry in seeded_ids
            or number in seed_numbers
            or type(number) is not int
            or number < 1
            or not positions
            or not positions <= set(available)
        ):
            raise ValueError(
                "Seeded entries require unique entry/seed and valid approved allowed_positions"
            )
        seed_numbers.add(number)
        seeded_ids.add(entry)
        allowed[entry] &= positions
    fixed = rules.get("fixed_positions", {})
    fixed_slots = [str(x) for x in fixed.values()]
    if len(fixed_slots) != len(set(fixed_slots)):
        raise ValueError("Duplicate fixed position")
    for entry, position in fixed.items():
        if entry not in allowed or str(position) not in allowed[entry]:
            raise ValueError("Fixed position is invalid or conflicts with permitted slots")
        allowed[entry] = {str(position)}
    pool_for_slot = {}
    cursor = 0
    for index, size in enumerate(plan.get("pool_sizes", []) if is_pool else []):
        for position in slot_ids[cursor : cursor + size]:
            pool_for_slot[position] = index
        cursor += size
    if scope == "pool" and not is_pool and mode != "disabled":
        raise ValueError("Pool avoidance requires a pool format")
    max_attempts = rules.get("max_attempts", 1000)
    if type(max_attempts) is not int or not 1 <= max_attempts <= 100000:
        raise ValueError("max_attempts must be within 1..100000")
    rng = random.Random(random_seed)
    best = None
    best_cost = None
    attempts = 0
    for attempts in range(1, max_attempts + 1):
        # Constrained entries first; randomized ties and positions remain isolated from global RNG.
        order = sorted(_shuffle(entries, rng), key=lambda e: len(allowed[e]))
        allocation = {}
        occupied: set[str] = set()
        for entry in order:
            options = _shuffle(sorted(allowed[entry] - occupied), rng)
            if not options:
                break
            allocation[entry] = options[0]
            occupied.add(options[0])
        if len(allocation) != len(entries):
            continue
        violations = []
        for index, first in enumerate(entries):
            for second in entries[index + 1 :]:
                if mode == "disabled":
                    continue
                p, q = allocation[first], allocation[second]
                same_scope = (
                    pool_for_slot[p] == pool_for_slot[q]
                    if scope == "pool"
                    else (int(p) - 1) // 2 == (int(q) - 1) // 2
                )
                shared = set(inputs["affiliations"][first]) & set(inputs["affiliations"][second])
                if same_scope and shared:
                    violations.append(
                        {"entry_ids": [first, second], "affiliation_ids": sorted(shared), "cost": 1}
                    )
        cost = len(violations)
        if mode == "hard" and cost:
            continue
        if best_cost is None or cost < best_cost:
            best, best_cost = (allocation, violations), cost
        if cost == 0:
            break
    output: dict[str, Any] | None = None
    if best is not None:
        allocation, violations = best
        by_slot = {p: e for e, p in allocation.items()}
        slots = [{"slot_id": p, "entry_id": by_slot.get(p)} for p in slot_ids]
        output = {
            "slots": slots,
            "pools": [],
            "bracket_entry_ids": [],
            "soft_violations": violations,
            "soft_cost": best_cost,
        }
        if is_pool:
            output["pools"] = [
                {
                    "label": chr(65 + i),
                    "entry_ids": [by_slot[p] for p in slot_ids if pool_for_slot[p] == i],
                }
                for i in range(len(plan["pool_sizes"]))
            ]
        else:
            output["bracket_entry_ids"] = [by_slot.get(p) for p in slot_ids]
    record = {
        "id": new_id(),
        "division_id": division_id,
        "input_hash": canonical_hash(inputs),
        "inputs": inputs,
        "format_basis_hash": format_basis(plan),
        "algorithm_version": ALGORITHM_VERSION,
        "python_runtime": platform.python_version(),
        "random_seed": random_seed,
        "constraints": rules,
        "output": output,
        "actor_id": actor_id,
        "created_at": utcnow().isoformat(),
        "reason": reason,
        "attempts": attempts,
        "status": "review_required",
        "search_status": "found" if output else "not_found_within_limit",
        "incomplete_affiliation_entry_ids": sorted(
            e for e in entries if not inputs["affiliations"][e]
        ),
    }
    snapshot.draws.append(record)
    return record
