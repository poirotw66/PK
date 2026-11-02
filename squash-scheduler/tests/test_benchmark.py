"""The benchmark fixture must exercise both people and real resource-consuming matches."""

from collections import Counter

from examples.benchmark import create_fixture
from squash_scheduler.domain.config import EventConfig
from squash_scheduler.domain.matches import validate_graph
from squash_scheduler.domain.models import EntrySide
from squash_scheduler.domain.validation import validate


def test_benchmark_uses_actual_import_and_balanced_complete_divisions(tmp_path):
    snapshot, report = create_fixture(tmp_path, division_count=2)
    assert len(snapshot.participants) == 6
    assert len(snapshot.matches) == 10
    assert not snapshot.assignments
    assert len(snapshot.imports) == 1 and snapshot.imports[0]["status"] == "applied"
    assert snapshot.sources[0]["sha256"]
    assert all(r.source_ref.get("source_file_id") for r in snapshot.registrations)
    assert report["appearance_distribution"] == {3: 4, 4: 2}
    assert Counter(m.kind for m in snapshot.matches) == {"competitive": 6, "exhibition": 4}
    assert not validate_graph(snapshot)
    assert validate(snapshot, "G3")["passed"]
    EventConfig.model_validate(snapshot.config)
    for division in snapshot.divisions:
        entries = {e.id for e in snapshot.entries if e.effective_division_id == division.id}
        games = [m for m in snapshot.matches if m.division_id == division.id]
        official_pairs = {
            frozenset((m.side_a.entry_id, m.side_b.entry_id))
            for m in games
            if m.kind == "competitive"
            and isinstance(m.side_a, EntrySide)
            and isinstance(m.side_b, EntrySide)
        }
        assert len(official_pairs) == 3 and set().union(*official_pairs) == entries
        assert all(m.side_a != m.side_b for m in games)
