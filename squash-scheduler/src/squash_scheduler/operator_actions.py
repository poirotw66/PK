"""Repository-free, previewable court operations and reviewed entry changes.

Planned assignments remain publication evidence. Actual starts are explicit human
events, never inferred from the wall clock, and constrain subsequent replanning.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from .domain.matches import possible_participants
from .domain.models import Assignment, Snapshot, canonical_hash, new_id, utcnow
from .domain.rankings import resolve_side
from .domain.revisions import approve
from .domain.validation import target_basis, validate
from .domain.withdrawals import apply_change, propose_change, register_reserve
from .scheduling.constraints import (
    build_context,
    minute,
    rest_minutes,
    transfer_minutes,
    validate_schedule,
)
from .scheduling.diagnostics import impacted_match_ids
from .scheduling.solver import solve_schedule


def _now() -> datetime:
    return utcnow().replace(second=0, microsecond=0)


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None or result.second or result.microsecond:
        raise ValueError("現場時間必須包含時區且以整分鐘記錄。")
    return result


def _identity(operation: dict[str, Any]) -> tuple[str, str]:
    actor, reason = operation.get("actor"), operation.get("reason")
    if (
        not isinstance(actor, str)
        or not actor.strip()
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("操作需要填寫操作人與原因。")
    return actor, reason


def _branch(snapshot: Snapshot) -> list[dict[str, Any]]:
    # Restoration already copies the target branch's changes. Its audit marker
    # must not discard that target's legitimate calls or pending delays.
    return snapshot.changes


def _basis(snapshot: Snapshot, match: Any) -> str:
    return canonical_hash(
        {
            "sides": [match.side_a.model_dump(mode="json"), match.side_b.model_dump(mode="json")],
            "entries": [resolve_side(snapshot, match.side_a), resolve_side(snapshot, match.side_b)],
        }
    )


def _live(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    matches = {m.id: m for m in snapshot.matches}
    assigned = {a.match_id: a for a in snapshot.assignments}
    result: dict[str, dict[str, Any]] = {}
    for event in _branch(snapshot):
        if event.get("kind") != "live_match_event":
            continue
        mid = event.get("match_id")
        if not isinstance(mid, str):
            continue
        match = matches.get(mid)
        if match is None or event.get("qualification_hash") != _basis(snapshot, match):
            continue
        row = result.setdefault(
            mid,
            {
                "called_at": None,
                "started_at": None,
                "delay_minutes": 0,
                "delay_active": False,
                "earliest_start_at": None,
            },
        )
        row["change_id"] = event["id"]
        action = event.get("event")
        if action in {"call", "start"}:
            assignment = assigned.get(mid)
            if assignment is None or assignment.court_id != event.get("court_id"):
                continue
            row["called_at" if action == "call" else "started_at"] = event["requested_at"]
        elif action == "delay":
            row.update(
                delay_minutes=event["delay_minutes"],
                delay_active=True,
                earliest_start_at=event["earliest_start_at"],
            )
        elif action == "clear_delay":
            row.update(delay_minutes=0, delay_active=False, earliest_start_at=None)
        if match.status in {"completed", "cancelled", "excluded"}:
            row["delay_active"] = False
    return result


def state_fields(snapshot: Snapshot) -> dict[str, Any]:
    live = _live(snapshot)
    matches = {m.id: m for m in snapshot.matches}
    courts = sorted(snapshot.config.get("courts", []), key=lambda c: c.get("sort_order", 0))
    board = []
    for court in courts:
        rows = []
        for assignment in sorted(snapshot.assignments, key=lambda a: (a.start_at, a.match_id)):
            if assignment.court_id != court["id"] or assignment.match_id not in matches:
                continue
            match = matches[assignment.match_id]
            rows.append(
                {
                    "match_id": match.id,
                    "status": match.status,
                    "planned_start_at": assignment.start_at.isoformat(),
                    "called_at": None,
                    "started_at": None,
                    "delay_minutes": 0,
                    "delay_active": False,
                    "earliest_start_at": None,
                    "change_id": None,
                    **live.get(match.id, {}),
                }
            )
        board.append({"id": court["id"], "label": court["label"], "matches": rows})
    targets = []
    for change in snapshot.changes:
        if change.get("kind") != "entry_change" or change.get("status") not in {
            "draft",
            "approved",
        }:
            continue
        operation = change["operations"][0]
        targets.append(
            {
                "id": change["id"],
                "division_id": change["impact"]["division_id"],
                "entry_id": operation["entry_id"],
                "replacement_entry_id": operation.get("replacement_entry_id"),
                "kind": operation["action"],
                "reason": change["reason"],
                "status": change["status"],
                "impact": deepcopy(change["impact"]),
            }
        )
    cancelled = {e.id for e in snapshot.entries if e.status == "cancelled"}
    reserves = sorted(
        {
            c["entry_id"]
            for c in snapshot.changes
            if c.get("kind") == "reserve_entry" and c.get("entry_id") in cancelled
        }
    )
    assigned = {a.match_id for a in snapshot.assignments}
    return {
        "live_board": {"courts": board, "unassigned_match_ids": sorted(set(matches) - assigned)},
        "entry_review_targets": targets,
        "reserve_entry_ids": reserves,
    }


def _actual_end(
    snapshot: Snapshot, match_id: str, row: dict[str, Any], at: datetime, context: Any
) -> datetime:
    start = _time(row["started_at"])
    end = start + timedelta(minutes=context.timing[match_id]["estimated_play_minutes"])
    match = context.matches[match_id]
    if match.status == "in_progress":
        return max(end, at + timedelta(minutes=context.grid))
    for result in snapshot.results:
        if (
            result.get("match_id") == match_id
            and result.get("status") == "confirmed"
            and result.get("created_at")
        ):
            completion = datetime.fromisoformat(result["created_at"])
            if completion.utcoffset() is not None:
                rounded = completion.replace(second=0, microsecond=0) + timedelta(
                    minutes=int(bool(completion.second or completion.microsecond))
                )
                end = max(end, rounded)
    return end


def schedule_live_options(snapshot: Snapshot, at: datetime | None = None) -> dict[str, Any]:
    """Convert manual facts into additional solver constraints; no facts means no changes."""
    live = _live(snapshot)
    if not live:
        return {}
    current = at or _now()
    context, issues = build_context(snapshot)
    if context is None or issues:
        raise ValueError("現場重排需要有效的賽事圖與排程設定。")
    assigned = {a.match_id: a for a in snapshot.assignments}
    lower = {
        m.id: current
        for m in context.matches.values()
        if m.status not in {"in_progress", "completed"}
    }
    blocks: dict[str, list[tuple[datetime, datetime]]] = {}
    for mid, row in live.items():
        if mid not in context.matches:
            continue
        if row.get("delay_active") and mid in lower:
            lower[mid] = max(lower[mid], _time(row["earliest_start_at"]))
        if not row.get("started_at") or mid not in assigned:
            continue
        assignment = assigned[mid]
        end = _actual_end(snapshot, mid, row, current, context)
        blocks.setdefault(assignment.court_id, []).append(
            (
                _time(row["started_at"]),
                end + timedelta(minutes=context.timing[mid]["court_buffer_minutes"]),
            )
        )
        for other, previous_lower in lower.items():
            if context.candidates.get(mid, set()) & context.candidates.get(other, set()):
                lower[other] = max(
                    previous_lower,
                    end
                    + timedelta(
                        minutes=rest_minutes(context, mid, other)
                        + transfer_minutes(context, mid, other)
                    ),
                )
    return {"not_before_by_match": lower, "blocked_court_intervals": blocks}


def validate_live_schedule(snapshot: Snapshot, at: datetime | None = None) -> list[str]:
    options = schedule_live_options(snapshot, at)
    if not options:
        return []
    matches = {m.id: m for m in snapshot.matches}
    errors = []
    for assignment in snapshot.assignments:
        match = matches.get(assignment.match_id)
        if match is None or match.status in {"in_progress", "completed", "cancelled", "excluded"}:
            continue
        lower = options["not_before_by_match"].get(match.id)
        if lower is not None and assignment.start_at < lower:
            errors.append(f"{match.id}: 開賽早於現場可用時間。")
        for start, end in options["blocked_court_intervals"].get(assignment.court_id, []):
            if assignment.start_at < end and start < assignment.court_release_at:
                errors.append(f"{match.id}: 場地與實際比賽占用衝突。")
    return errors


def _change(snapshot: Snapshot, change_id: str) -> dict[str, Any]:
    change = next((c for c in snapshot.changes if c.get("id") == change_id), None)
    if change is None:
        raise ValueError("找不到變更紀錄。")
    return change


def _scope(snapshot: Snapshot, change_id: str) -> set[str]:
    change = _change(snapshot, change_id)
    if change.get("kind") == "entry_change":
        if change.get("status") != "approved":
            raise ValueError("退賽或遞補需先核准影響範圍。")
        seeds = set(change["impact"]["affected_match_ids"])
    elif change.get("kind") == "live_match_event" and change in _branch(snapshot):
        seeds = {change["match_id"]}
    else:
        raise ValueError("此變更不支援局部重排。")
    active = {m.id for m in snapshot.matches if m.status not in {"cancelled", "excluded"}}
    return impacted_match_ids(snapshot, seeds) & active


def _graph(snapshot: Snapshot) -> None:
    report = validate(snapshot, "G3")
    errors = [
        i["message"]
        for i in report["issues"]
        if i["severity"] == "error" and i["status"] == "open" and i["gate"] in {"G2", "G3"}
    ]
    if errors:
        raise ValueError("賽事圖尚未通過核准：" + "; ".join(errors))


def prepare_action(snapshot: Snapshot, operation: dict[str, Any]) -> dict[str, Any]:
    """Freeze exact proposals and times before both preview and confirmation execution."""
    prepared = deepcopy(operation)
    actor, reason = _identity(prepared)
    action = prepared.get("action")
    if action == "live_match":
        prepared["requested_at"] = _now().isoformat()
    elif action == "entry_change":
        prepared["proposal"] = propose_change(
            snapshot,
            prepared["entry_id"],
            prepared["kind"],
            prepared.get("replacement_entry_id"),
            reason,
            actor,
        )
    elif action == "approve_entry_change":
        prepared["approval_basis"] = target_basis(_change(snapshot, prepared["change_id"]))
    elif action == "local_replan":
        _graph(snapshot)
        scope = _scope(snapshot, prepared["change_id"])
        requested_at = _now()
        prepared["requested_at"] = requested_at.isoformat()
        result = solve_schedule(
            snapshot.model_copy(deep=True),
            local_match_ids=scope,
            **schedule_live_options(snapshot, requested_at),
        )
        if result.get("status") not in {"FEASIBLE", "OPTIMAL"}:
            raise ValueError("局部重排無可用解：" + str(result.get("status", "unknown")))
        prepared["assignments"] = result["assignments"]
        prepared["affected_match_ids"] = sorted(scope)
    elif action != "reserve":
        raise ValueError("不支援的賽務操作。")
    return prepared


def _live_action(snapshot: Snapshot, operation: dict[str, Any]) -> tuple[Snapshot, dict[str, Any]]:
    actor, reason = _identity(operation)
    at = _time(operation["requested_at"])
    if not timedelta(0) <= _now() - at <= timedelta(minutes=2):
        raise ValueError("現場預覽已超過兩分鐘，請重新預覽。")
    working = snapshot.model_copy(deep=True)
    match = next((m for m in working.matches if m.id == operation["match_id"]), None)
    if match is None or match.status in {"completed", "cancelled", "excluded"}:
        raise ValueError("比賽不存在或已結束。")
    assignment = next((a for a in working.assignments if a.match_id == match.id), None)
    if assignment is None:
        raise ValueError("比賽尚未有有效場地排程。")
    event = operation.get("event")
    if event not in {"call", "start", "delay", "clear_delay"}:
        raise ValueError("不支援的現場事件。")
    if match.status == "in_progress":
        raise ValueError("已開賽比賽不得重複叫號、開賽或修改延誤。")
    delay = operation.get("delay_minutes", 0)
    if type(delay) is not int or delay < 0 or delay > 1440 or (event == "delay" and delay == 0):
        raise ValueError("延誤分鐘需為 1 至 1440 的整數。")
    if event in {"call", "start"}:
        _graph(working)
        issues = validate_schedule(working)
        if issues:
            raise ValueError("現有排程尚未通過驗證，請先修正或重排。")
        entries = [resolve_side(working, side) for side in (match.side_a, match.side_b)]
        if (
            None in entries
            or len(set(entries)) != 2
            or any(
                not any(e.id == eid and e.status == "active" for e in working.entries)
                for eid in entries
            )
        ):
            raise ValueError("對戰雙方尚未確認或不具參賽資格。")
        context, _ = build_context(working)
        if context is None:
            raise ValueError("賽事排程設定不完整。")
        candidates = possible_participants(working)
        for other in working.matches:
            if other.id == match.id or other.status != "in_progress":
                continue
            other_assignment = next(
                (a for a in working.assignments if a.match_id == other.id), None
            )
            if other_assignment is not None and other_assignment.court_id == assignment.court_id:
                raise ValueError("此場地已有進行中的比賽。")
            if candidates.get(match.id, set()) & candidates.get(other.id, set()):
                raise ValueError("選手正在其他場地比賽。")
        if event == "start":
            if at < assignment.start_at:
                raise ValueError("尚未到預定開賽時間。")
            live = _live(working)
            if context.max_matches_per_day is not None:
                day = at.astimezone(context.zone).date()
                people = candidates.get(match.id, set())
                counts = {person: 0 for person in people}
                for prior in context.matches.values():
                    if prior.id == match.id or prior.status not in {"in_progress", "completed"}:
                        continue
                    prior_assignment = next(
                        (a for a in working.assignments if a.match_id == prior.id), None
                    )
                    recorded = live.get(prior.id, {}).get("started_at")
                    prior_start = (
                        _time(recorded)
                        if recorded
                        else prior_assignment.start_at
                        if prior_assignment
                        else None
                    )
                    if (
                        prior_start is not None
                        and prior_start.astimezone(context.zone).date() == day
                    ):
                        for person in people & candidates.get(prior.id, set()):
                            counts[person] += 1
                if any(count >= context.max_matches_per_day for count in counts.values()):
                    raise ValueError("選手當日已達出賽場數上限，不能開賽；請檢視實際日期及重排。")
            if live.get(match.id, {}).get("delay_active") and at < _time(
                live[match.id]["earliest_start_at"]
            ):
                raise ValueError("尚未到延誤後可開賽時間。")
            for dependency in context.dependencies.get(match.id, set()):
                if context.matches[dependency].status != "completed":
                    raise ValueError("晉級前置比賽尚未完成。")
            for prior in working.matches:
                if (
                    prior.id == match.id
                    or prior.status != "completed"
                    or not (candidates.get(match.id, set()) & candidates.get(prior.id, set()))
                ):
                    continue
                previous = next((a for a in working.assignments if a.match_id == prior.id), None)
                if previous is None:
                    raise ValueError("選手前場缺少時間紀錄，無法確認休息。")
                row = live.get(prior.id, {})
                end = (
                    _actual_end(working, prior.id, row, at, context)
                    if row.get("started_at")
                    else previous.play_end_at
                )
                rest = rest_minutes(context, match.id, prior.id) + (
                    transfer_minutes(context, match.id, prior.id)
                    if previous.court_id != assignment.court_id
                    else 0
                )
                if at < end + timedelta(minutes=rest):
                    raise ValueError("選手休息或換場時間不足。")
            # Actual start may be off-grid; keep the planned assignment immutable.
            # Reject late starts that would collide with an unmodified future assignment.
            end = at + timedelta(minutes=context.timing[match.id]["estimated_play_minutes"])
            release = end + timedelta(minutes=context.timing[match.id]["court_buffer_minutes"])
            if not any(
                start <= minute(at) and minute(release) <= stop
                for start, stop in context.windows[assignment.court_id]
            ):
                raise ValueError("實際開賽時間超出場地可用時段或遇到封場。")
            for upcoming in working.assignments:
                upcoming_match = context.matches.get(upcoming.match_id)
                if (
                    upcoming_match is None
                    or upcoming_match.id == match.id
                    or upcoming_match.status in {"completed", "cancelled", "excluded"}
                ):
                    continue
                if (
                    upcoming.court_id == assignment.court_id
                    and upcoming.start_at < release
                    and at < upcoming.court_release_at
                ):
                    raise ValueError("實際開賽將占用下一場場地，請先標示延誤並重排。")
                if candidates.get(match.id, set()) & candidates.get(upcoming_match.id, set()):
                    required = end + timedelta(
                        minutes=rest_minutes(context, match.id, upcoming_match.id)
                        + (
                            transfer_minutes(context, match.id, upcoming_match.id)
                            if upcoming.court_id != assignment.court_id
                            else 0
                        )
                    )
                    if upcoming.start_at < required:
                        raise ValueError("實際開賽將影響選手後續休息，請先局部重排。")
            match.status = "in_progress"
        else:
            match.status = "ready"
    audit = {
        "id": new_id(),
        "kind": "live_match_event",
        "match_id": match.id,
        "event": event,
        "requested_at": at.isoformat(),
        "actor_id": actor,
        "reason": reason,
        "court_id": assignment.court_id,
        "qualification_hash": _basis(working, match),
        "delay_minutes": delay if event == "delay" else 0,
        "earliest_start_at": (max(assignment.start_at, at) + timedelta(minutes=delay)).isoformat()
        if event == "delay"
        else None,
    }
    working.changes.append(audit)
    return working, {
        "title": {
            "call": "叫號",
            "start": "確認開賽",
            "delay": "標示延誤",
            "clear_delay": "清除延誤",
        }[event],
        "match_id": match.id,
        "event": event,
        "requested_at": at.isoformat(),
        "delay_minutes": audit["delay_minutes"],
        "notice": "已記錄人工現場狀態；預定排程保留原始時間。",
    }


def execute_action(
    snapshot: Snapshot, operation: dict[str, Any]
) -> tuple[Snapshot, dict[str, Any]]:
    """Execute only an exact prepared operation; never persist or rerun a proposed solver."""
    actor, reason = _identity(operation)
    action = operation.get("action")
    if action == "live_match":
        return _live_action(snapshot, operation)
    working = snapshot.model_copy(deep=True)
    if action == "reserve":
        result = register_reserve(working, operation["entry_id"], actor, reason)
        working.changes.append(
            {"id": new_id(), "kind": "reserve_entry", "created_at": utcnow().isoformat(), **result}
        )
        return working, {"title": "登記候補", **result}
    if action == "entry_change":
        working, audit = apply_change(working, operation["proposal"])
        return working, {
            "title": "退賽提案" if operation["kind"] == "withdraw" else "遞補提案",
            "entry_id": operation["entry_id"],
            "replacement_entry_id": operation.get("replacement_entry_id"),
            "impact": audit["impact"],
            "notice": "需再核准影響範圍後才可重新排程。",
        }
    if action == "approve_entry_change":
        audit = _change(working, operation["change_id"])
        working, _ = approve(
            working, "entry_change:" + audit["id"], operation["approval_basis"], actor, reason
        )
        _graph(working)
        return working, {
            "title": "核准退賽／遞補影響",
            "change_id": audit["id"],
            "impact": audit["impact"],
            "notice": "影響範圍已核准，可預覽局部重排。",
        }
    if action == "local_replan":
        _graph(working)
        scope = _scope(working, operation["change_id"])
        if sorted(scope) != operation["affected_match_ids"]:
            raise ValueError("局部重排影響範圍已變更。")
        before = {a.match_id: a for a in working.assignments}
        working.assignments = [Assignment.model_validate(a) for a in operation["assignments"]]
        after = {a.match_id: a for a in working.assignments}
        for mid, assignment in before.items():
            if mid not in scope and after.get(mid) != assignment:
                raise ValueError("局部重排不得修改影響範圍外的排程。")
        errors = [
            i.message for i in validate_schedule(working, baseline_assignments=snapshot.assignments)
        ]
        errors.extend(validate_live_schedule(working))
        if errors:
            raise ValueError("局部重排驗證失敗：" + "; ".join(errors))
        summary = {
            "title": "確認局部重排",
            "change_id": operation["change_id"],
            "affected_match_ids": sorted(scope),
            "assignment_count": len(working.assignments),
            "changed_count": sum(a != before.get(a.match_id) for a in working.assignments),
            "assignments": [a.model_dump(mode="json") for a in working.assignments],
        }
        return working, summary
    raise ValueError("不支援的賽務操作。")
