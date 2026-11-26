"""Explicit format recommendations and approved parameter validation."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from pydantic import TypeAdapter

from .models import Issue, MatchSide, Snapshot, canonical_hash

# TypeAdapter accepts an Annotated union schema at runtime; its overload models classes only.
SIDE_ADAPTER: TypeAdapter[MatchSide] = TypeAdapter(cast(Any, MatchSide))

TEMPLATES = {
    "round_robin",
    "single_elimination",
    "pools_knockout",
    "pools_3x3_top2_knockout6_bronze",
}


def format_basis(plan: dict[str, Any]) -> str:
    return canonical_hash({k: v for k, v in plan.items() if k not in {"status", "approval_id"}})


def active_entries(snapshot: Snapshot, division_id: str) -> list[str]:
    return sorted(
        e.id
        for e in snapshot.entries
        if e.effective_division_id == division_id and e.status == "active"
    )


def pending_format_divisions(snapshot: Snapshot) -> set[str]:
    """Operator-confirmed formats still missing a generated match graph."""
    generated = {match.division_id for match in snapshot.matches}
    return {
        d.id
        for d in snapshot.divisions
        if "encouragement_enabled" in (d.format_plan or {})
        and d.status not in {"cancelled", "merged", "exhibition_only"}
        and d.id not in generated
        and len(active_entries(snapshot, d.id)) >= 2
    }


def recommend_formats(snapshot: Snapshot) -> dict[str, Any]:
    """Return alternatives; recommendations never select or approve a format."""
    result = {}
    for division in snapshot.divisions:
        n = len(active_entries(snapshot, division.id))
        choices: list[dict[str, Any]] = []
        if n >= 2:
            choices = [
                {
                    "template_id": "round_robin",
                    "template_version": "1.0.0",
                    "estimated_matches": n * (n - 1) // 2,
                    "missing": [],
                    "reason": "Every entry plays every other entry once.",
                },
                {
                    "template_id": "single_elimination",
                    "template_version": "1.0.0",
                    "bracket_capacity": 1 << (n - 1).bit_length(),
                    "estimated_matches": n - 1,
                    "missing": ["approved draw rules", "placement_matches"],
                    "reason": "A champion requires n-1 played matches; byes advance automatically.",
                },
                {
                    "template_id": "pools_knockout",
                    "template_version": "1.0.0",
                    "estimated_matches": None,
                    "missing": [
                        "pool_sizes",
                        "advancement_counts",
                        "bracket_capacity",
                        "placement_matches",
                        "approved draw rules",
                    ],
                    "reason": "Pool sizes and qualification counts must be chosen explicitly.",
                },
            ]
        if n == 9:
            choices.append(
                {
                    "template_id": "pools_3x3_top2_knockout6_bronze",
                    "template_version": "1.0.0",
                    "pool_sizes": [3, 3, 3],
                    "advancement_counts": [2, 2, 2],
                    "bracket_capacity": 8,
                    "placement_matches": ["bronze"],
                    "estimated_matches": 15,
                    "missing": ["approved draw rules"],
                    "reason": "Synthetic example only; historical positions remain unknown.",
                }
            )
        requested_placements = (division.format_plan or {}).get("placement_matches", [])
        unknown_placements = (
            [item for item in requested_placements if isinstance(item, str) and item != "bronze"]
            if isinstance(requested_placements, list)
            else []
        )
        for choice in choices:
            choice["missing"].extend(
                f"explicit placement definition for {label}" for label in unknown_placements
            )
        result[division.id] = {"entry_count": n, "recommendations": choices}
    return result


def validate_formats(snapshot: Snapshot) -> list[Issue]:
    issues = []
    for division in snapshot.divisions:
        if division.status in {"cancelled", "merged", "exhibition_only"}:
            continue
        n = len(active_entries(snapshot, division.id))
        if n == 0:
            continue
        plan = division.format_plan
        errors = []
        if not plan:
            errors.append("Format plan is missing.")
        else:
            template = plan.get("template_id")
            if template not in TEMPLATES or plan.get("template_version") != "1.0.0":
                errors.append("Unknown format template or version.")
            if plan.get("status") != "approved" or not plan.get("approval_id"):
                issues.append(
                    Issue(
                        code="FORMAT_APPROVAL_REQUIRED",
                        gate="G2",
                        entity_type="division",
                        entity_id=division.id,
                        message="The current format requires explicit approval.",
                    )
                )
            if "encouragement_enabled" in plan:
                from .encouragement import division_eligible

                encouragement = plan["encouragement_enabled"]
                if type(encouragement) is not bool or (
                    encouragement
                    and (
                        template != "single_elimination"
                        or not division_eligible(snapshot, division)
                    )
                ):
                    errors.append("Encouragement requires an eligible direct knockout format.")
            placement = plan.get("placement_matches", [])
            if not isinstance(placement, list):
                errors.append(
                    "Placement matches must be an array of bronze or explicit definitions."
                )
                placement = []
            placement_ids = set()
            stage_labels: dict[str | None, str] = {}
            bronze_count = 0
            for definition in placement:
                if definition == "bronze":
                    bronze_count += 1
                    continue
                try:
                    if not isinstance(definition, dict):
                        raise TypeError("Unknown placement label requires an explicit definition")
                    required = {"id", "label", "side_a", "side_b", "round", "bracket_position"}
                    if not required <= definition.keys() or definition.keys() - required - {
                        "code",
                        "stage_key",
                        "stage_label",
                    }:
                        raise ValueError("Explicit placement fields are missing or unsupported")
                    identifier = str(UUID(definition["id"]))
                    if identifier in placement_ids:
                        raise ValueError("Explicit placement IDs must be unique")
                    placement_ids.add(identifier)
                    for field in ("stage_key", "stage_label"):
                        if field in definition and (
                            not isinstance(definition[field], str) or not definition[field].strip()
                        ):
                            raise ValueError(f"Explicit placement {field} must be nonempty")
                    key = definition.get("stage_key")
                    if "stage_label" in definition:
                        label = definition["stage_label"]
                        if key in stage_labels and stage_labels[key] != label:
                            raise ValueError("Explicit placement stage labels conflict for one key")
                        stage_labels[key] = label
                    if not isinstance(definition["label"], str) or not definition["label"].strip():
                        raise ValueError("Explicit placement label must be nonempty")
                    if "code" in definition and (
                        not isinstance(definition["code"], str) or not definition["code"].strip()
                    ):
                        raise ValueError("Explicit placement code must be nonempty when provided")
                    if type(definition["round"]) is not int or definition["round"] < 1:
                        raise ValueError("Explicit placement round must be a positive integer")
                    if (
                        type(definition["bracket_position"]) is not int
                        or definition["bracket_position"] < 0
                    ):
                        raise ValueError(
                            "Explicit placement bracket position must be a nonnegative integer"
                        )
                    SIDE_ADAPTER.validate_python(definition["side_a"])
                    SIDE_ADAPTER.validate_python(definition["side_b"])
                except (ValueError, TypeError, AttributeError) as exc:
                    errors.append(f"Invalid explicit placement definition: {exc}")
            if bronze_count > 1:
                errors.append("Bronze placement may appear only once.")
            qualifiers = n
            if template in {"pools_knockout", "pools_3x3_top2_knockout6_bronze"}:
                sizes = plan.get("pool_sizes")
                counts = plan.get("advancement_counts")
                if (
                    not isinstance(sizes, list)
                    or not sizes
                    or any(type(x) is not int or x < 2 for x in sizes)
                    or sum(sizes) != n
                ):
                    errors.append("Pool sizes must partition all active entries.")
                if (
                    not isinstance(counts, list)
                    or not isinstance(sizes, list)
                    or len(counts) != len(sizes)
                    or any(
                        type(c) is not int or c < 1 or type(s) is not int or c > s
                        for c, s in zip(counts or [], sizes or [])
                    )
                ):
                    errors.append(
                        "Each pool needs an explicit positive advancement count within its size."
                    )
                else:
                    qualifiers = sum(counts)
                if template == "pools_3x3_top2_knockout6_bronze" and (
                    sizes != [3, 3, 3]
                    or counts != [2, 2, 2]
                    or plan.get("bracket_capacity") != 8
                    or placement != ["bronze"]
                ):
                    errors.append(
                        "The synthetic 3x3 template requires top two, capacity eight, and bronze."
                    )
            if template != "round_robin":
                capacity = plan.get("bracket_capacity")
                if (
                    type(capacity) is not int
                    or capacity < 2
                    or capacity & (capacity - 1)
                    or capacity < qualifiers
                ):
                    errors.append(
                        "Bracket capacity must be a power of two large enough for qualifiers."
                    )
                if qualifiers < 2:
                    errors.append("Elimination requires at least two qualifiers.")
                if "bronze" in placement and qualifiers < 4:
                    errors.append("Bronze requires two actually played semifinals.")
            elif "bronze" in placement:
                errors.append("Round robin has no implicit bronze placement match.")
        for message in errors:
            issues.append(
                Issue(
                    code="FORMAT_INCOMPATIBLE",
                    gate="G2",
                    entity_type="division",
                    entity_id=division.id,
                    field_path="format_plan",
                    message=message,
                )
            )
    return issues
