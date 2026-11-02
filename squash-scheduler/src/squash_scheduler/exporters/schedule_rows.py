"""Keep the printed schedule's source column order separate from bracket geometry."""

from __future__ import annotations

from typing import Any


def schedule_display(row: dict[str, Any]) -> dict[str, Any]:
    """Source evidence may print opponents in an order different from the drawn tree."""
    if "source_schedule_sides" not in row:
        return row
    sides = row["source_schedule_sides"]
    if (
        not isinstance(sides, list)
        or len(sides) != 2
        or any(value is not None and not isinstance(value, str) for value in sides)
    ):
        raise ValueError("Source schedule must contain exactly two literal display strings")
    return {**row, "side_a": sides[0] or "原稿未載", "side_b": sides[1] or "原稿未載"}
