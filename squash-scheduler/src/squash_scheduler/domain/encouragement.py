"""Read-only eligibility audit for the organizer's junior-high consolation rule."""

from .models import ByeSide, EntrySide, Snapshot

ELIGIBLE_DIVISIONS = {"國中男子組", "國中女子組"}
RULE_TEXT = "鼓勵賽僅限國中男、女組非種子球員，且須為籤表第一輪實際出賽的敗者；首輪輪空、棄權或未到場者不得參加。"


def eligibility_report(snapshot: Snapshot, division_id: str) -> dict:
    division = next(d for d in snapshot.divisions if d.id == division_id)
    rules = {**snapshot.config.get("draw_defaults", {}), **division.draw_overrides}
    seed_rows = rules.get("seeded_entries", [])
    seed_rows_valid = isinstance(seed_rows, list) and all(
        isinstance(s, dict) and isinstance(s.get("entry_id"), str) for s in seed_rows
    )
    seed_ids = {s["entry_id"] for s in seed_rows} if seed_rows_valid else set()
    division_entries = {e.id for e in snapshot.entries if e.effective_division_id == division_id}
    seed_mode = rules.get("seeded_entries_mode")
    seeds_known = (seed_mode == "none" and not seed_rows) or (
        seed_mode == "provided"
        and seed_rows_valid
        and bool(seed_rows)
        and len(seed_ids) == len(seed_rows)
        and seed_ids <= division_entries
    )
    stages = [
        s for s in snapshot.stages if s.division_id == division_id and s.type == "elimination"
    ]
    stage = min(stages, key=lambda s: (s.order, s.id)) if stages else None
    rounds = [
        m
        for m in snapshot.matches
        if stage
        and m.stage_id == stage.id
        and m.round == 1
        and m.kind == "competitive"
        and m.status not in {"excluded", "cancelled"}
    ]
    results: dict[str, list[dict]] = {}
    for result in snapshot.results:
        if result.get("status") == "confirmed":
            results.setdefault(result["match_id"], []).append(result)
    registrations = {r.id: r for r in snapshot.registrations}
    people = {p.id: p for p in snapshot.participants}
    rows = []
    for entry in snapshot.entries:
        if entry.effective_division_id != division_id:
            continue
        registration = registrations[entry.registration_id]
        name = people[registration.participant_id].display_name
        status, reason, source = "ineligible", "DIVISION_NOT_ELIGIBLE", None
        if (division.format_plan or {}).get("encouragement_enabled") is False:
            reason = "ENCOURAGEMENT_DISABLED"
        elif division.name in ELIGIBLE_DIVISIONS and division.category == "school":
            status, reason = "pending", "FIRST_ROUND_UNRESOLVED"
            if entry.status != "active":
                status, reason = "ineligible", "ENTRY_NOT_ACTIVE"
            elif entry.id in seed_ids:
                status, reason = "ineligible", "SEEDED_ENTRY"
            elif not seeds_known:
                reason = "SEEDS_UNCONFIRMED"
            elif stage:
                appearances = [
                    m
                    for m in rounds
                    if any(
                        isinstance(s, EntrySide) and s.entry_id == entry.id
                        for s in (m.side_a, m.side_b)
                    )
                ]
                if len(appearances) == 1:
                    match = appearances[0]
                    source = match.id
                    if any(isinstance(s, ByeSide) for s in (match.side_a, match.side_b)):
                        status, reason = "ineligible", "FIRST_ROUND_BYE"
                    elif (
                        isinstance(match.side_a, EntrySide)
                        and isinstance(match.side_b, EntrySide)
                        and len(results.get(match.id, [])) == 1
                        and match.status == "completed"
                    ):
                        result = results[match.id][0]
                        sides = {match.side_a.entry_id, match.side_b.entry_id}
                        winner = result.get("winner_id")
                        outcome = result.get("outcome")
                        if outcome in {"walkover", "double_forfeit"}:
                            status, reason = "ineligible", "UNPLAYED_FIRST_ROUND"
                        elif winner == entry.id and outcome in {"played", "retired"}:
                            status, reason = "ineligible", "FIRST_ROUND_WINNER"
                        elif outcome == "played" and winner in sides:
                            status, reason = "eligible", "FIRST_ROUND_LOSER"
                        elif outcome == "retired":
                            reason = "RETIREMENT_REQUIRES_REVIEW"
                elif (
                    not appearances
                    and rounds
                    and all(isinstance(s, EntrySide) for m in rounds for s in (m.side_a, m.side_b))
                ):
                    status, reason = "ineligible", "NO_FIRST_ROUND_APPEARANCE"
        rows.append(
            {
                "entry_id": entry.id,
                "participant_id": registration.participant_id,
                "name": name,
                "status": status,
                "reason": reason,
                "first_round_match_id": source,
            }
        )
    return {
        "division_id": division_id,
        "division_name": division.name,
        "rule": RULE_TEXT,
        "entries": rows,
        "eligible_entry_ids": [r["entry_id"] for r in rows if r["status"] == "eligible"],
        "pending_entry_ids": [r["entry_id"] for r in rows if r["status"] == "pending"],
        "draw_created": False,
    }
