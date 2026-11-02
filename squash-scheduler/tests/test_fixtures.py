"""Saved anonymous fixtures must be usable contracts with the specified graph invariants."""

from pathlib import Path

from squash_scheduler.domain.matches import round_robin_counts
from squash_scheduler.domain.models import Snapshot
from squash_scheduler.domain.validation import validate


def test_saved_nine_player_fixture_has_fifteen_independently_valid_assignments():
    path = Path(__file__).parent / "fixtures/synthetic/nine-entries.json"
    snapshot = Snapshot.model_validate_json(path.read_text("utf-8"))
    assert len(snapshot.participants) == 9 and len(snapshot.matches) == 15
    assert len(snapshot.assignments) == 15
    assert validate(snapshot, "G4")["passed"]


def test_saved_five_player_exception_preserves_pair_and_unequal_counts():
    path = Path(__file__).parent / "fixtures/synthetic/five-round-robin-exclusion.json"
    snapshot = Snapshot.model_validate_json(path.read_text("utf-8"))
    assert len(snapshot.matches) == 10
    assert sum(m.status != "excluded" for m in snapshot.matches) == 9
    counts = round_robin_counts(snapshot, snapshot.stages[0].id)
    assert sorted(c["expected"] - c["excluded"] for c in counts.values()) == [3, 3, 4, 4, 4]
    assert validate(snapshot, "G3")["passed"]
