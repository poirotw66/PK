"""Reviewed per-match competition rules; historical playing conditions stay fixed."""

from copy import deepcopy

from .models import Match, Snapshot


def effective_match_rules(snapshot: Snapshot, match: Match) -> dict:
    from .rankings import effective_rules

    frozen = (match.rule_snapshot or {}).get("match_rules")
    if match.status in {"in_progress", "completed"} and frozen:
        return deepcopy(frozen)
    rules = effective_rules(snapshot, match.stage_id)
    if snapshot.config.get("runtime", {}).get("coach_competition_rules"):
        rules["best_of_games"] = 5 if is_championship(snapshot, match) else 3
        walkover = rules.get("walkover_rule")
        if not isinstance(walkover, dict) or not {
            "include_in_rankings",
            "counts_as_played",
            "winner_points",
            "loser_points",
        } <= set(walkover):
            rules["walkover_rule"] = {
                "include_in_rankings": True,
                "counts_as_played": False,
                "winner_points": rules["scoring"]["win_points"],
                "loser_points": rules["scoring"]["forfeit_points"],
            }
        denominator = rules.get("ranking_denominator_rule")
        if not isinstance(denominator, dict) or type(denominator.get("walkover")) is not bool:
            rules["ranking_denominator_rule"] = {
                **(denominator if isinstance(denominator, dict) else {}),
                "walkover": False,
            }
    return rules


def is_championship(snapshot: Snapshot, match: Match) -> bool:
    stage = next(s for s in snapshot.stages if s.id == match.stage_id)
    if (
        stage.type != "elimination"
        or stage.stage_key == "encouragement"
        or "鼓勵" in (stage.label or "")
        or match.kind != "competitive"
    ):
        return False
    rounds = [
        m.round for m in snapshot.matches if m.stage_id == stage.id and m.kind == "competitive"
    ]
    return bool(rounds) and match.round == max(rounds)
