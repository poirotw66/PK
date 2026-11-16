"""Match numbers for a new schedule, before its numbering is frozen."""

from datetime import UTC, datetime

from .models import Snapshot


def chronological_match_numbers(snapshot: Snapshot) -> dict[str, str]:
    """Number by scheduled instant and configured court order; unassigned matches come last."""
    assignments = {assignment.match_id: assignment for assignment in snapshot.assignments}
    court_order = {court["id"]: court["sort_order"] for court in snapshot.config.get("courts", [])}

    def order(match_id: str) -> tuple[bool, datetime, int, str, str]:
        assignment = assignments.get(match_id)
        if assignment is None:
            return (True, datetime.max.replace(tzinfo=UTC), 0, "", match_id)
        return (
            False,
            assignment.start_at,
            court_order[assignment.court_id],
            assignment.court_id,
            match_id,
        )

    return {
        match_id: str(number)
        for number, match_id in enumerate(sorted((m.id for m in snapshot.matches), key=order), 1)
    }
