"""Reviewed competition formats and match estimates for the local operator."""

from .domain.draws import draw_inputs
from .domain.encouragement import ELIGIBLE_DIVISIONS
from .domain.formats import active_entries, format_basis, pending_format_divisions, validate_formats
from .domain.matches import dependency_map, generate_matches
from .domain.models import Division, PoolRankSide, Snapshot, canonical_hash, stable_id
from .domain.revisions import approve
from .domain.validation import approval_valid, target_basis

INACTIVE = {"cancelled", "merged", "exhibition_only"}
TEMPLATES = {"round_robin", "single_elimination", "pools_knockout"}
NOTICE_PENDING = "賽制已確認，請完成待處理組別的抽籤與籤表確認，再重新排程。"


def _encouragement_allowed(division: Division) -> bool:
    return division.name in ELIGIBLE_DIVISIONS and division.category == "school"


def _affected(snapshot: Snapshot, division: Division) -> tuple[set[str], set[str], set[str]]:
    stages = {s.id for s in snapshot.stages if s.division_id == division.id}
    pools = {p.id for p in snapshot.pools if p.stage_id in stages}
    matches = {m.id for m in snapshot.matches if m.division_id == division.id}
    return stages, pools, matches


def _editable(snapshot: Snapshot, division: Division, n: int) -> bool:
    if snapshot.status == "approved" or n < 2 or division.status in INACTIVE:
        return False
    stages, _, matches = _affected(snapshot, division)
    return not (
        any(
            m.id in matches
            and (
                m.status != "planned"
                or m.kind == "exhibition"
                or (m.kind == "placement" and m.id != stable_id("bronze", m.stage_id))
            )
            for m in snapshot.matches
        )
        or any(a.match_id in matches and any(a.locks.values()) for a in snapshot.assignments)
        or any(r.get("match_id") in matches for r in snapshot.results)
        or any(r.get("match_id") in matches for r in snapshot.exclusions)
        or any(r.get("stage_id") in stages for r in snapshot.ranking_decisions)
        or any(
            r.get("kind") == "live_match_event" and r.get("match_id") in matches
            for r in snapshot.changes
        )
        or any(
            d.get("division_id") == division.id
            and d.get("kind") in {"deferred_slots", "encouragement"}
            and d.get("status") != "superseded"
            for d in snapshot.draws
        )
        or any(
            isinstance(item, dict)
            for item in (division.format_plan or {}).get("placement_matches", [])
        )
    )


def format_state(snapshot: Snapshot, division: Division) -> dict:
    n = len(active_entries(snapshot, division.id))
    plan = division.format_plan or {}
    template = plan.get("template_id", "")
    sizes, advances = plan.get("pool_sizes") or [], plan.get("advancement_counts") or []
    pools = [{"size": size, "advances": advance} for size, advance in zip(sizes, advances)]
    placement = plan.get("placement_matches", [])
    bronze = "bronze" in placement
    allowed = _encouragement_allowed(division)
    encouragement = (
        allowed and template == "single_elimination" and plan.get("encouragement_enabled", True)
    )
    main_matches = None
    if division.status in INACTIVE or n < 2:
        main_matches = 0
    elif template == "round_robin":
        main_matches = n * (n - 1) // 2 + len(placement)
    elif template == "single_elimination":
        main_matches = n - 1 + len(placement)
    elif template.startswith("pools_") and pools and sum(sizes) == n:
        main_matches = sum(size * (size - 1) // 2 for size in sizes) + sum(advances) - 1
        main_matches += len(placement)
    capacity = plan.get("bracket_capacity")
    first_round = max(0, n - capacity // 2) if encouragement and type(capacity) is int else 0
    return {
        "template_id": "pools_knockout" if template.startswith("pools_") else template,
        "pools": pools,
        "bronze": bronze,
        "encouragement": bool(encouragement),
        "encouragement_allowed": allowed,
        "editable": _editable(snapshot, division, n),
        "entry_count": n,
        "main_matches": main_matches,
        "encouragement_max": max(0, first_round - 1),
        "pending_draw": division.id in pending_format_divisions(snapshot),
    }


def generate_when_formats_ready(snapshot: Snapshot) -> bool:
    """Wait for independent draw confirmations before rebuilding the whole match graph."""
    if validate_formats(snapshot):
        return False
    for division in snapshot.divisions:
        if division.status in INACTIVE or not active_entries(snapshot, division.id):
            continue
        plan = division.format_plan or {}
        if plan.get("template_id") == "round_robin":
            continue
        draw = next(
            (
                d
                for d in reversed(snapshot.draws)
                if d.get("division_id") == division.id
                and d.get("kind") not in {"deferred_slots", "encouragement"}
            ),
            {},
        )
        if (
            draw.get("status") != "approved"
            or not draw.get("approval_id")
            or not draw.get("output")
            or draw.get("format_basis_hash") != format_basis(plan)
            or draw.get("input_hash")
            != canonical_hash(draw_inputs(snapshot, division.id, draw["random_seed"]))
        ):
            return False
    generate_matches(snapshot)
    return True


def _bye_slots(snapshot: Snapshot, division: Division, capacity: int, n: int) -> list[int]:
    rules = {**snapshot.config.get("draw_defaults", {}), **division.draw_overrides}
    reserved = (
        {
            str(slot)
            for seed in rules.get("seeded_entries", [])
            for slot in seed.get("allowed_positions", [])
        }
        | {
            str(slot)
            for positions in rules.get("permitted_slots", {}).values()
            for slot in positions
        }
        | {str(slot) for slot in rules.get("fixed_positions", {}).values()}
    )
    slots = []
    for first in range(1, capacity + 1, 2):
        for slot in (first + 1, first):
            if str(slot) not in reserved:
                slots.append(slot)
                break
    byes = capacity - n
    if len(slots) < byes:
        raise ValueError("現有種子或指定籤位限制無法分配輪空，請先調整籤位限制。")
    return slots[:byes]


def configure_format(
    snapshot: Snapshot, division_id: str, selection: object, actor: str, reason: str
):
    division = next((d for d in snapshot.divisions if d.id == division_id), None)
    if division is None:
        raise ValueError("找不到此組別。")
    n = len(active_entries(snapshot, division.id))
    if not _editable(snapshot, division, n):
        raise ValueError("此組已核准、開打、鎖定，或含有自訂對戰或已確認紀錄，無法修改賽制。")
    if not isinstance(selection, dict) or set(selection) != {
        "template_id",
        "pools",
        "bronze",
        "encouragement",
    }:
        raise ValueError("賽制資料欄位不完整或含不支援的欄位。")
    template, pools = selection["template_id"], selection["pools"]
    bronze, encouragement = selection["bronze"], selection["encouragement"]
    if (
        not isinstance(template, str)
        or template not in TEMPLATES
        or not isinstance(pools, list)
        or len(pools) > n
        or type(bronze) is not bool
        or type(encouragement) is not bool
    ):
        raise ValueError("請選擇有效的賽制及選項。")
    if encouragement and (template != "single_elimination" or not _encouragement_allowed(division)):
        raise ValueError("鼓勵賽僅限採直接單淘汰的國中男、女組。")
    if template != "pools_knockout" and pools:
        raise ValueError("只有分池賽制可以設定小組。")
    if any(
        not isinstance(pool, dict)
        or set(pool) != {"size", "advances"}
        or type(pool["size"]) is not int
        or pool["size"] not in (3, 4)
        or type(pool["advances"]) is not int
        or not 1 <= pool["advances"] <= pool["size"]
        for pool in pools
    ):
        raise ValueError("每池須為三人或四人，晉級人數須在一人到該池人數之間。")
    if template == "pools_knockout" and sum(pool["size"] for pool in pools) != n:
        raise ValueError(f"分池合計 {sum(pool['size'] for pool in pools)} 人，須配滿本組 {n} 人。")
    qualifiers = sum(pool["advances"] for pool in pools) if pools else n
    if template != "round_robin" and qualifiers < 2:
        raise ValueError("淘汰賽至少需要兩人。")
    if bronze and (template == "round_robin" or qualifiers < 4):
        raise ValueError("季殿賽需要至少四名淘汰賽選手。")
    capacity = 1 << (qualifiers - 1).bit_length()
    plan = {
        "template_id": template,
        "template_version": "1.0.0",
        "status": "draft",
        "placement_matches": ["bronze"] if bronze else [],
        "encouragement_enabled": encouragement,
    }
    if template != "round_robin":
        plan["bracket_capacity"] = capacity
    if pools:
        plan["pool_sizes"] = [pool["size"] for pool in pools]
        plan["advancement_counts"] = [pool["advances"] for pool in pools]
        plan["qualifier_positions"] = [
            value
            for i in range(qualifiers)
            for value in ([i, None] if i < capacity - qualifiers else [i])
        ]
    existing = format_state(snapshot, division)
    if (
        all(existing[key] == selection[key] for key in selection)
        and division.format_plan
        and approval_valid(snapshot, "format", division_id, division.format_plan)
    ):
        return snapshot, {
            "title": "預覽組別賽制",
            "division_id": division_id,
            "format": format_state(snapshot, division),
            "notice": "賽制沒有變更。",
            "can_apply": False,
        }

    _, pool_ids, match_ids = _affected(snapshot, division)
    if any(
        mid not in match_ids and dependencies & match_ids
        for mid, dependencies in dependency_map(snapshot).items()
    ) or any(
        m.id not in match_ids
        and any(
            isinstance(side, PoolRankSide) and side.pool_id in pool_ids
            for side in (m.side_a, m.side_b)
        )
        for m in snapshot.matches
    ):
        raise ValueError("其他組別仍引用此組籤表，請先解除跨組晉級依賴。")
    if template == "single_elimination" and capacity > n:
        division.draw_overrides["bye_allocation_rule"] = {
            "positions": _bye_slots(snapshot, division, capacity, n)
        }
    division.format_plan = plan
    snapshot.matches = [m for m in snapshot.matches if m.id not in match_ids]
    snapshot.assignments = [a for a in snapshot.assignments if a.match_id not in match_ids]
    snapshot.pools = [p for p in snapshot.pools if p.id not in pool_ids]
    for draw in snapshot.draws:
        if draw.get("division_id") == division_id:
            draw["status"] = "superseded"
    working, _ = approve(snapshot, "format:" + division_id, target_basis(plan), actor, reason)
    errors = [
        issue.message for issue in validate_formats(working) if issue.entity_id == division_id
    ]
    if errors:
        raise ValueError("；".join(errors))
    generated = generate_when_formats_ready(working)
    saved = next(d for d in working.divisions if d.id == division_id)
    notice = "儲存後請重新排程。" if generated else NOTICE_PENDING
    if match_ids:
        notice = f"此組原有 {len(match_ids)} 場對戰及其排程會清除。{notice}"
    return working, {
        "title": "確認組別賽制",
        "division_id": division_id,
        "format": format_state(working, saved),
        "notice": notice,
    }
