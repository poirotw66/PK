"""Independent constructive result replay for an unplayed qualification witness.

No DNF or path-counting implementation is reused. Actual result validation must
accept the hypothetical scores and resolve the same entrant into every witness match.
"""

from __future__ import annotations

from collections import Counter

from squash_scheduler.domain.models import Snapshot
from squash_scheduler.domain.rankings import import_results, resolve_side


def replay_witness(snapshot: Snapshot, person: str, witness: dict) -> dict:
    if any(r.get("status") == "confirmed" for r in snapshot.results):
        return {
            "verified": False,
            "reason": "Constructive replay is limited to an unplayed source; existing confirmed results require score-aware analysis",
        }
    registrations = {r.id: r.participant_id for r in snapshot.registrations}
    owners = {e.id: registrations[e.registration_id] for e in snapshot.entries}
    divisions = {e.effective_division_id for e in snapshot.entries if owners[e.id] == person}
    working = snapshot.model_copy(deep=True)
    working.matches = [
        m
        for m in working.matches
        if m.division_id in divisions
        and m.status not in {"cancelled", "excluded"}
        and m.side_a.type != "bye"
        and m.side_b.type != "bye"
    ]
    matches = {m.id: m for m in working.matches}
    decisions = witness["decisions"]
    orders = {}
    member_pool = {}
    for pool in working.pools:
        if pool.stage_id not in {m.stage_id for m in working.matches}:
            continue
        own = [entry for entry in pool.entry_ids if owners[entry] == person]
        order = sorted(entry for entry in pool.entry_ids if entry not in own)
        if own:
            rank = int(decisions.get(f"pool_rank:{pool.id}", len(pool.entry_ids)))
            order.insert(rank - 1, own[0])
        orders[pool.id] = order
        for entry in pool.entry_ids:
            member_pool[(pool.stage_id, entry)] = pool.id
    winners: dict[str, str] = {}
    losers: dict[str, str] = {}
    visiting = set()

    def side_entry(side):
        if side.type == "entry":
            return side.entry_id
        if side.type == "pool_rank":
            return orders[side.pool_id][side.rank - 1]
        if side.type in {"match_winner", "match_loser"}:
            play(side.match_id)
            return (winners if side.type == "match_winner" else losers)[side.match_id]
        if side.type == "draw_slot":
            entry = resolve_side(snapshot, side)
            if entry is not None:
                return entry
        raise ValueError("Constructive result replay does not guess an unexecuted future draw")

    def play(mid):
        if mid in winners:
            return
        if mid in visiting:
            raise ValueError("Replay graph contains a cycle")
        visiting.add(mid)
        match = matches[mid]
        pair = [side_entry(match.side_a), side_entry(match.side_b)]
        if pair[0] == pair[1] or owners[pair[0]] == owners[pair[1]]:
            raise ValueError("Replay puts one participant on both sides")
        choice = decisions.get(f"winner_side:{mid}")
        if choice is not None:
            index = 0 if choice == "a" else 1
        elif (
            pool_id := member_pool.get((match.stage_id, pair[0]))
        ) is not None and member_pool.get((match.stage_id, pair[1])) == pool_id:
            order = orders[pool_id]
            index = 0 if order.index(pair[0]) < order.index(pair[1]) else 1
        else:
            index = 0
        winners[mid], losers[mid] = pair[index], pair[1 - index]
        visiting.remove(mid)

    batch = []
    try:
        for match in working.matches:
            play(match.id)
            pair = [side_entry(match.side_a), side_entry(match.side_b)]
            rules = (match.rule_snapshot or {})["match_rules"]
            target = max(rules["points_to_win_game"], rules["win_by_points"])
            if rules["point_cap_mode"] == "enabled":
                target = min(target, rules["point_cap"])
            game = {
                "side_a": target if winners[match.id] == pair[0] else 0,
                "side_b": target if winners[match.id] == pair[1] else 0,
            }
            batch.append(
                {
                    "match_id": match.id,
                    "status": "confirmed",
                    "outcome": "played",
                    "winner_id": winners[match.id],
                    "games": [game.copy() for _ in range(rules["best_of_games"] // 2 + 1)],
                }
            )
        accepted = import_results(
            working, {"actor_id": "hypothetical-path-replay", "results": batch}
        )
        actual = {
            m.id
            for m in working.matches
            if any(
                owners.get(resolve_side(working, side) or "") == person
                for side in (m.side_a, m.side_b)
            )
        }
        expected = set(witness["match_ids"])
        if actual != expected:
            raise ValueError(
                f"Replayed appearances differ: extra={sorted(actual - expected)}, missing={sorted(expected - actual)}"
            )
        names = {d.id: d.name for d in working.divisions}
        return {
            "verified": True,
            "accepted_hypothetical_results": accepted["imported_count"],
            "match_count": len(actual),
            "match_ids": sorted(actual),
            "by_division": dict(Counter(names[matches[mid].division_id] for mid in actual)),
            "interpretation": "在記憶體複本輸入完整假想成績，通過既有成績／排名規則後，確認同一位選手確實進入這些場次；未寫入來源。",
        }
    except (ValueError, KeyError, StopIteration) as exc:
        return {"verified": False, "reason": str(exc)}
