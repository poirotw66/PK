from squash_scheduler.domain.coach_tiebreak import order_tied_entries


def game(a, b, values):
    return {"a": a, "b": b, "games": [{"side_a": x, "side_b": y} for x, y in values]}


def test_unrelated_opponent_does_not_change_quotient():
    records = [game("a", "b", [(11, 5), (11, 7)]), game("a", "c", [(0, 11)] * 3)]
    assert order_tied_entries(["b", "a"], records) == (["a", "b"], [])


def test_three_way_game_quotient_precedes_points():
    records = [game("a", "b", [(11, 0), (11, 0)]), game("b", "c", [(11, 0), (11, 0)]), game("c", "a", [(11, 9), (9, 11), (11, 9)])]
    assert order_tied_entries(["c", "b", "a"], records) == (["a", "b", "c"], [])


def test_point_quotient_resolves_equal_game_quotients():
    records = [game("a", "b", [(11, 1), (11, 1)]), game("b", "c", [(11, 2), (11, 2)]), game("c", "a", [(11, 9), (11, 9)])]
    assert order_tied_entries(["c", "b", "a"], records) == (["a", "b", "c"], [])


def test_equal_or_unplayed_stays_for_manual_decision():
    records = [game("a", "b", [(11, 5)] * 2), game("b", "c", [(11, 5)] * 2), game("c", "a", [(11, 5)] * 2)]
    assert order_tied_entries(["a", "b", "c"], records)[1] == [["a", "b", "c"]]
    assert order_tied_entries(["a", "b"], [])[1] == [["a", "b"]]
