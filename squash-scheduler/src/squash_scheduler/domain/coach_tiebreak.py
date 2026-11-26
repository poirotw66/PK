"""Exact tied-subgroup quotients; unrelated opponents never affect a tie break."""

from fractions import Fraction
from typing import Any


def order_tied_entries(
    ids: list[str], records: list[dict[str, Any]]
) -> tuple[list[str], list[list[str]]]:
    members = set(ids)
    totals = {entry: [0, 0, 0, 0] for entry in ids}
    for record in records:
        a, b = record["a"], record["b"]
        if a not in members or b not in members:
            continue
        for game in record["games"]:
            x, y = game["side_a"], game["side_b"]
            totals[a][2] += x
            totals[a][3] += y
            totals[b][2] += y
            totals[b][3] += x
            if max(x, y) == max(record.get("target", 11), min(x, y) + record.get("margin", 2)):
                totals[a][0] += int(x > y)
                totals[a][1] += int(y > x)
                totals[b][0] += int(y > x)
                totals[b][1] += int(x > y)

    def quotient(numerator: int, denominator: int) -> tuple[int, Fraction] | None:
        if denominator:
            return 1, Fraction(numerator, denominator)
        return (2, Fraction(0)) if numerator else None

    keys = {
        entry: (
            quotient(totals[entry][0], totals[entry][1]),
            quotient(totals[entry][2], totals[entry][3]),
        )
        for entry in ids
    }
    if any(key[0] is None for key in keys.values()):
        return ids[:], [ids[:]]
    ordered = sorted(
        ids, key=lambda entry: (keys[entry][0], keys[entry][1] or (0, Fraction(0))), reverse=True
    )
    ties: list[list[str]] = []
    for entry in ordered:
        if ties and keys[ties[-1][0]] == keys[entry]:
            ties[-1].append(entry)
        else:
            ties.append([entry])
    return ordered, [group for group in ties if len(group) > 1]
