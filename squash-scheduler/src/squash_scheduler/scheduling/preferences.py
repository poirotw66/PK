"""Soft opening-time preferences for an initial schedule."""

from __future__ import annotations

from collections import defaultdict

from pydantic import TypeAdapter

from squash_scheduler.domain.config import PreferredScheduleWindow
from squash_scheduler.domain.models import Snapshot

from .constraints import ScheduleContext, minute


def preferred_starts(
    snapshot: Snapshot, context: ScheduleContext
) -> dict[str, list[tuple[int, int]]]:
    raw = (snapshot.config.get("runtime") or {}).get("preferred_schedule_windows", [])
    windows = TypeAdapter(list[PreferredScheduleWindow]).validate_python(raw)
    by_stage: dict[tuple[str, str], list[PreferredScheduleWindow]] = defaultdict(list)
    division_ids = {division.id for division in snapshot.divisions}
    for window in windows:
        day = window.start_at.astimezone(context.zone).date()
        if window.division_id not in division_ids or day not in context.anchors:
            raise ValueError("Preferred start windows require a known division and event date")
        if window.end_at.astimezone(context.zone).date() != day:
            raise ValueError("A preferred start window must fit within one event day")
        by_stage[window.division_id, window.stage_type].append(window)
    stage_types = {stage.id: stage.type for stage in snapshot.stages}
    return {
        mid: ranges
        for mid, match in context.matches.items()
        if (
            ranges := [
                (minute(window.start_at), minute(window.end_at))
                for window in by_stage.get((match.division_id, stage_types[match.stage_id]), [])
                if window.rounds is None or match.round in window.rounds
            ]
        )
    }


def in_preferred_window(start: int, windows: list[tuple[int, int]]) -> bool:
    return any(begin <= start < end for begin, end in windows)
