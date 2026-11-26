"""Reviewed junior-high consolation draws, with current qualification evidence."""

from __future__ import annotations

import random
from copy import deepcopy

from .encouragement import division_eligible, eligibility_report
from .models import (
    ID,
    Issue,
    LoserSide,
    Match,
    MatchSide,
    Model,
    Snapshot,
    Stage,
    canonical_hash,
    content_hash,
    new_id,
    stable_id,
    utcnow,
)
from .validation import approval_valid, target_basis

KIND = "encouragement"
ALGORITHM = "first-round-losers-shuffle-distributed-byes-v1"
CLOUD_ALGORITHM = "first-round-losers-mulberry32-distributed-byes-v1"


def is_encouragement(stage: Stage) -> bool:
    return stage.stage_key == KIND or "鼓勵" in (stage.label or "")


def is_encouragement_definition(value: object) -> bool:
    return isinstance(value, dict) and (
        value.get("stage_key") == KIND
        or any(
            isinstance(value.get(key), str) and "鼓勵" in value[key]
            for key in ("label", "stage_label")
        )
    )


def _source(snapshot: Snapshot, division_id: str) -> tuple[dict, str]:
    from .draws import draw_inputs
    from .rankings import _validate_result, effective_rules

    division = next((d for d in snapshot.divisions if d.id == division_id), None)
    if division is None or not division_eligible(snapshot, division):
        raise ValueError("ENCOURAGEMENT_DIVISION_INVALID: only junior-high boys/girls qualify")
    plan = division.format_plan or {}
    if plan.get("encouragement_enabled") is False:
        raise ValueError("ENCOURAGEMENT_DISABLED: this division does not offer consolation")
    if plan.get("template_id") != "single_elimination":
        raise ValueError(
            "ENCOURAGEMENT_SOURCE_UNSUPPORTED: a reviewed first-round knockout is required"
        )
    if not approval_valid(snapshot, "format", division_id, plan):
        raise ValueError("ENCOURAGEMENT_SOURCE_UNAPPROVED: main format approval is required")
    draws = [
        d
        for d in snapshot.draws
        if d.get("division_id") == division_id
        and d.get("kind") not in {KIND, "deferred_slots"}
        and d.get("status") != "superseded"
    ]
    if len(draws) != 1:
        raise ValueError("ENCOURAGEMENT_SOURCE_UNAPPROVED: one current main draw is required")
    draw = draws[0]
    if (
        draw.get("status") != "approved"
        or not approval_valid(snapshot, "draw", draw["id"], draw)
        or draw.get("input_hash")
        != canonical_hash(draw_inputs(snapshot, division_id, draw["random_seed"]))
    ):
        raise ValueError("ENCOURAGEMENT_SOURCE_UNAPPROVED: main draw inputs or approval changed")
    stage_id = stable_id("stage", division_id, "elimination")
    first = sorted(
        (
            m
            for m in snapshot.matches
            if m.stage_id == stage_id and m.round == 1 and m.kind == "competitive"
        ),
        key=lambda m: m.id,
    )
    # Verify the actual first-round topology against the approved draw, including byes.
    from .matches import elimination

    expected = [
        m
        for m in elimination(
            draw["output"]["bracket_entry_ids"],
            division_id,
            stage_id,
            bracket_capacity=plan["bracket_capacity"],
        )
        if m.round == 1
    ]

    def topology(match: Match) -> dict:
        return {
            key: match.model_dump(mode="json")[key]
            for key in (
                "id",
                "division_id",
                "stage_id",
                "kind",
                "side_a",
                "side_b",
                "round",
                "bracket_position",
            )
        }

    if sorted(map(canonical_hash, map(topology, first))) != sorted(
        map(canonical_hash, map(topology, expected))
    ):
        raise ValueError(
            "ENCOURAGEMENT_SOURCE_STALE: first-round graph differs from the approved draw"
        )
    report = eligibility_report(snapshot, division_id)
    if report["pending_entry_ids"]:
        raise ValueError(
            "ENCOURAGEMENT_ELIGIBILITY_PENDING: confirm seeds and all first-round outcomes"
        )
    source_ids = {m.id for m in first}
    results = [
        r
        for r in snapshot.results
        if r.get("match_id") in source_ids and r.get("status") == "confirmed"
    ]
    for match in first:
        evidence = [r for r in results if r["match_id"] == match.id]
        if match.status != "completed" or len(evidence) != 1:
            raise ValueError(
                "ENCOURAGEMENT_ELIGIBILITY_PENDING: all first-round matches need confirmed outcomes"
            )
        _validate_result(
            snapshot,
            match,
            evidence[0],
            evidence[0].get("rule_snapshot") or effective_rules(snapshot, stage_id),
        )
    basis = canonical_hash(
        {
            "rule_version": ALGORITHM,
            "report": report,
            "main_draw": target_basis(draw),
            "first_round": [m.model_dump(mode="json") for m in first],
            "results": sorted(results, key=lambda r: r["match_id"]),
        }
    )
    return report, basis


def _output(snapshot: Snapshot, division_id: str, seed: int, algorithm: str = ALGORITHM) -> dict:
    if type(seed) is not int:
        raise ValueError("An integer draw seed is required")
    report, basis = _source(snapshot, division_id)
    entries = sorted(report["eligible_entry_ids"])
    shuffled = entries.copy()
    if algorithm == ALGORITHM:
        random.Random(seed).shuffle(shuffled)
    elif algorithm == CLOUD_ALGORITHM and 0 <= seed <= 0xFFFFFFFF:
        value = seed
        for index in range(len(shuffled) - 1, 0, -1):
            value = (value + 0x6D2B79F5) & 0xFFFFFFFF
            mixed = ((value ^ (value >> 15)) * (value | 1)) & 0xFFFFFFFF
            mixed ^= (mixed + (((mixed ^ (mixed >> 7)) * (mixed | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
            selected = int(((mixed ^ (mixed >> 14)) & 0xFFFFFFFF) / 4294967296 * (index + 1))
            shuffled[index], shuffled[selected] = shuffled[selected], shuffled[index]
    else:
        raise ValueError("Unsupported encouragement shuffle algorithm or seed")
    capacity = 0 if len(entries) < 2 else 1 << (len(entries) - 1).bit_length()
    byes = capacity - len(entries) if capacity else 0
    bracket: list[str | None] = []
    for index, entry in enumerate(shuffled):
        bracket.append(entry)
        if index < byes:
            bracket.append(None)
    output = {
        "algorithm": algorithm,
        "qualification_basis_hash": basis,
        "entry_ids": entries,
        "bracket_entry_ids": bracket if capacity else [],
        "bracket_capacity": capacity,
        "bye_positions": [i + 1 for i, e in enumerate(bracket) if e is None],
        "match_count": max(0, len(entries) - 1),
        "disposition": "draw" if capacity else "no_competition",
        "qualification_report": report,
    }
    names = {r["entry_id"]: r["name"] for r in report["entries"]}
    source_names = {
        r["first_round_match_id"]: r["name"] for r in report["entries"] if r["status"] == "eligible"
    }
    _, matches = _record_geometry({"division_id": division_id, "output": output})
    codes = {m.id: m.display_code for m in matches}

    def label(side: MatchSide) -> str:
        if isinstance(side, LoserSide):
            return source_names[side.match_id]
        if side.type == "match_winner":
            return f"{codes[side.match_id]}勝者"
        raise ValueError("Unexpected encouragement side")

    output["preview"] = {
        "slots": [
            {"position": i + 1, "entry_id": e, "name": names[e] if e else "輪空"}
            for i, e in enumerate(bracket if capacity else [])
        ],
        "matches": [
            {
                "code": m.display_code,
                "round": m.round,
                "side_a": label(m.side_a),
                "side_b": label(m.side_b),
            }
            for m in matches
        ],
    }
    return output


def propose_draw(snapshot: Snapshot, division_id: str, seed: int, actor: str, reason: str) -> dict:
    if not actor.strip() or not reason.strip():
        raise ValueError("Draw proposal requires actor and reason")
    proposal = {
        "id": new_id(),
        "kind": KIND,
        "base_revision_id": snapshot.id,
        "base_content_hash": content_hash(snapshot),
        "division_id": division_id,
        "random_seed": seed,
        "actor_id": actor,
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "output": _output(snapshot, division_id, seed),
    }
    return {**proposal, "proposal_hash": canonical_hash(proposal)}


def _record_geometry(record: dict) -> tuple[Stage, list[Match]]:
    from .matches import elimination

    division_id, output = record["division_id"], record["output"]
    stage = Stage(
        id=stable_id("placement-stage", division_id, KIND),
        division_id=division_id,
        type="placement",
        stage_key=KIND,
        label="鼓勵賽",
        order=100,
    )
    if record.get("cloud_record"):
        stage.rule_set_id = stable_id("cloud-encouragement-rules", division_id)
    if output["disposition"] == "no_competition":
        return stage, []
    source_ids = {
        r["entry_id"]: r["first_round_match_id"]
        for r in output["qualification_report"]["entries"]
        if r["status"] == "eligible"
    }
    sides: list[MatchSide | str | None] = [
        LoserSide(match_id=source_ids[e]) if e else None for e in output["bracket_entry_ids"]
    ]
    matches = elimination(sides, division_id, stage.id, bracket_capacity=output["bracket_capacity"])
    prefix = (
        "國男鼓"
        if output["qualification_report"]["division_name"] == "國中男子組"
        else "國女鼓"
        if output["qualification_report"]["division_name"] == "國中女子組"
        else output["qualification_report"]["division_name"] + "鼓"
    )
    for number, match in enumerate(matches, 1):
        match.kind = "placement"
        match.display_code = f"{prefix}{number}"
    return stage, matches


def _current_records(snapshot: Snapshot) -> list[dict]:
    return [d for d in snapshot.draws if d.get("kind") == KIND and d.get("status") != "superseded"]


def _check_record(snapshot: Snapshot, record: dict) -> None:
    if record.get("status") != "approved" or not approval_valid(
        snapshot, "encouragement_draw", record["id"], record
    ):
        raise ValueError("ENCOURAGEMENT_DRAW_UNAPPROVED: confirmed draw evidence is required")
    if not any(
        a.get("id") == record.get("approval_id")
        and a.get("action") == "approve"
        and a.get("actor_id")
        and a.get("reason")
        for a in snapshot.approvals
    ):
        raise ValueError(
            "ENCOURAGEMENT_DRAW_UNAPPROVED: actor and explicit confirmation are required"
        )
    if record["output"] != _output(
        snapshot,
        record["division_id"],
        record["random_seed"],
        record["output"].get("algorithm", ALGORITHM),
    ):
        raise ValueError(
            "ENCOURAGEMENT_DRAW_STALE: qualification evidence changed; propose a new draw"
        )


def generated_draws(snapshot: Snapshot) -> tuple[list[Stage], list[Match]]:
    stages, matches = [], []
    seen = set()
    for record in _current_records(snapshot):
        if record["division_id"] in seen:
            raise ValueError("ENCOURAGEMENT_DRAW_DUPLICATE: multiple current draws in one division")
        seen.add(record["division_id"])
        _check_record(snapshot, record)
        stage, rows = _record_geometry(record)
        if rows:
            stages.append(stage)
            matches.extend(rows)
    return stages, matches


def validate_encouragement(snapshot: Snapshot) -> list[Issue]:
    issues = []
    recognized = {s.id for s in snapshot.stages if is_encouragement(s)}
    try:
        manual_ids = {
            d["id"]
            for division in snapshot.divisions
            for d in (division.format_plan or {}).get("placement_matches", [])
            if is_encouragement_definition(d)
        }
        if manual_ids:
            raise ValueError(
                "ENCOURAGEMENT_DRAW_UNAPPROVED: manual encouragement definitions must be replaced by a qualified draw"
            )
        stages, expected = generated_draws(snapshot)
        owned = {s.id for s in stages}
        if recognized - owned:
            raise ValueError(
                "ENCOURAGEMENT_DRAW_UNAPPROVED: encouragement stages require a confirmed eligible draw"
            )
        actual_stages = {s.id: s for s in snapshot.stages}
        for stage in stages:
            actual_stage = actual_stages.get(stage.id)
            if actual_stage is None or any(
                getattr(actual_stage, field) != getattr(stage, field)
                for field in ("division_id", "type", "stage_key", "label", "order")
            ):
                raise ValueError(
                    "ENCOURAGEMENT_GRAPH_INVALID: stage differs from the confirmed draw"
                )
        fields = (
            "id",
            "division_id",
            "stage_id",
            "kind",
            "side_a",
            "side_b",
            "round",
            "bracket_position",
        )

        def geometry(rows: list[Match]) -> list[str]:
            return sorted(
                canonical_hash({k: m.model_dump(mode="json")[k] for k in fields}) for m in rows
            )

        actual = [m for m in snapshot.matches if m.stage_id in recognized | owned]
        if geometry(actual) != geometry(expected):
            raise ValueError(
                "ENCOURAGEMENT_GRAPH_INVALID: matches differ from the confirmed eligible draw"
            )
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        issues.append(
            Issue(
                code="ENCOURAGEMENT_INVALID",
                gate="G3",
                entity_type="encouragement_draw",
                message=str(exc),
            )
        )
    return issues


def apply_draw(
    snapshot: Snapshot, proposal: dict, basis_hash: str, actor: str, reason: str
) -> tuple[Snapshot, dict]:
    """Explicit confirmation; stale/tampered proposals and protected redraws are atomic."""
    from .matches import generate_matches

    if not actor.strip() or not reason.strip():
        raise ValueError("Draw confirmation requires actor and reason")
    payload = {k: v for k, v in proposal.items() if k != "proposal_hash"}
    if basis_hash != proposal.get("proposal_hash") or basis_hash != canonical_hash(payload):
        raise ValueError(
            "ENCOURAGEMENT_PROPOSAL_INVALID: confirmation hash does not match proposal"
        )
    if proposal.get("base_revision_id") != snapshot.id or proposal.get(
        "base_content_hash"
    ) != content_hash(snapshot):
        raise ValueError(
            "ENCOURAGEMENT_PROPOSAL_STALE: regenerate proposal for the current revision"
        )

    # Validate identifier syntax before it can become an approval target.
    class Identifier(Model):
        id: ID

    Identifier(id=proposal["id"])
    if any(d.get("id") == proposal["id"] for d in snapshot.draws):
        raise ValueError("ENCOURAGEMENT_PROPOSAL_INVALID: draw identifier is already used")
    division_id = proposal["division_id"]
    if proposal.get("kind") != KIND or proposal["output"] != _output(
        snapshot, division_id, proposal["random_seed"]
    ):
        raise ValueError(
            "ENCOURAGEMENT_PROPOSAL_INVALID: proposal does not reproduce the eligible draw"
        )
    affected_stages = {
        s.id for s in snapshot.stages if s.division_id == division_id and is_encouragement(s)
    }
    affected = {m.id for m in snapshot.matches if m.stage_id in affected_stages}
    if (
        any(m.id in affected and m.status in {"in_progress", "completed"} for m in snapshot.matches)
        or any(r.get("match_id") in affected for r in snapshot.results)
        or any(a.match_id in affected and any(a.locks.values()) for a in snapshot.assignments)
    ):
        raise ValueError(
            "ENCOURAGEMENT_REDRAW_LOCKED: started, resulted or locked matches must be preserved"
        )
    division = next(d for d in snapshot.divisions if d.id == division_id)
    if any(
        is_encouragement_definition(d)
        for d in (division.format_plan or {}).get("placement_matches", [])
    ):
        raise ValueError(
            "ENCOURAGEMENT_LEGACY_DEFINITION: remove manual encouragement definitions through format review first"
        )
    working = snapshot.model_copy(deep=True)
    for previous in _current_records(working):
        if previous["division_id"] == division_id:
            previous["status"] = "superseded"
    record = {
        "id": proposal["id"],
        "kind": KIND,
        "division_id": division_id,
        "random_seed": proposal["random_seed"],
        "output": deepcopy(proposal["output"]),
        "proposal_hash": basis_hash,
        "actor_id": actor,
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "status": "approved",
    }
    approval = {
        "id": new_id(),
        "action": "approve",
        "target_type": "encouragement_draw",
        "target_id": record["id"],
        "basis_hash": target_basis(record),
        "actor_id": actor,
        "reason": reason,
        "created_at": utcnow().isoformat(),
    }
    record["approval_id"] = approval["id"]
    working.draws.append(record)
    working.approvals.append(approval)
    generate_matches(working)
    # All previous encouragement assignments are invalidated, including unprotected time/court choices.
    working.assignments = [a for a in working.assignments if a.match_id not in affected]
    working.status = "draft"
    return working, {
        "draw_id": record["id"],
        "disposition": record["output"]["disposition"],
        "entry_ids": record["output"]["entry_ids"],
        "match_ids": [
            m.id
            for m in working.matches
            if m.stage_id == stable_id("placement-stage", division_id, KIND)
        ],
        "cleared_assignment_ids": sorted(affected & {a.match_id for a in snapshot.assignments}),
    }
