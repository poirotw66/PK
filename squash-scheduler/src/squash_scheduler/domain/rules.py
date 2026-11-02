"""Explicit rule expansion for a draft; played matches keep their existing rules."""

from __future__ import annotations

from copy import deepcopy

from .models import Snapshot, canonical_hash


def refresh_rule_snapshots(snapshot: Snapshot, match_ids: set[str] | None = None) -> None:
    from ..scheduling.constraints import effective_timing
    from .rankings import effective_rules

    stages = {s.id: s for s in snapshot.stages}
    for match in snapshot.matches:
        if match_ids is not None and match.id not in match_ids:
            continue
        if match.status in {"in_progress", "completed"}:
            if match.rule_snapshot is None:
                # Never reconstruct unknown historical playing conditions from today's defaults.
                raise ValueError("Started match is missing its fixed rule snapshot")
            continue
        stage = stages[match.stage_id]
        expanded = {
            "match_rules": effective_rules(snapshot, stage.id),
            "timing": effective_timing(snapshot, match, frozen=False),
            "inheritance": {
                "division_id": match.division_id,
                "stage_id": stage.id,
                "rule_set_id": stage.rule_set_id,
            },
        }
        match.rule_snapshot = {**deepcopy(expanded), "basis_hash": canonical_hash(expanded)}
