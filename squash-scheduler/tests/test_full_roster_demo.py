"""The complete private source demo retains raw evidence and shared identity."""

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "full_roster_demo", ROOT / "examples" / "run_full_roster_demo.py"
)
assert SPEC is not None and SPEC.loader is not None
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


@pytest.fixture(scope="module")
def source_snapshot():
    if not (ROOT / "output/historical-inventory/import-report.json").exists():
        pytest.skip("Private original Word inventory is not included in portable installations")
    return DEMO.build_snapshot(ROOT)


def test_every_source_registration_keeps_raw_reference_and_exact_identity(source_snapshot):
    snapshot = source_snapshot
    imported = json.loads(
        (ROOT / "output/historical-inventory/import-report.json").read_text("utf-8")
    )
    candidates = {
        c["id"]: c for c in imported["candidates"] if c["status"] != "ignored_with_reason"
    }
    assert len(snapshot.registrations) == len(snapshot.entries) == len(candidates) == 147
    assert len(snapshot.participants) == 114
    assert len(snapshot.divisions) == 17
    registrations = {r.id: r for r in snapshot.registrations}
    people = {p.id: p for p in snapshot.participants}
    ids_by_exact_name = defaultdict(set)
    for candidate in snapshot.imports[0]["candidates"]:
        if candidate["id"] not in candidates:
            continue
        source = candidates[candidate["id"]]
        resolution = candidate["resolution"]
        registration = registrations[resolution["registration_id"]]
        assert registration.source_ref == source["source_ref"]
        assert registration.original_division_id == source["division_id"]
        assert people[registration.participant_id].official_name == source["official_name"]
        ids_by_exact_name[source["official_name"]].add(registration.participant_id)
    assert all(len(ids) == 1 for ids in ids_by_exact_name.values())
    assert snapshot.changes[0]["actor_id"] == "Justin-demo"
    assert all(not p.aliases for p in snapshot.participants)


def test_single_entry_groups_are_retained_without_invented_matches(source_snapshot):
    snapshot = source_snapshot
    single = [d for d in snapshot.divisions if d.status == "exhibition_only"]
    assert len(single) == 3
    for division in single:
        assert sum(e.effective_division_id == division.id for e in snapshot.entries) == 1
        assert not any(m.division_id == division.id for m in snapshot.matches)
    assert len(snapshot.matches) == 279
    assert len({m.display_code for m in snapshot.matches}) == 279
    assert all(m.display_code.startswith("DEMO-") for m in snapshot.matches)
    assert [c["label"] for c in snapshot.config["courts"]] == ["場地A", "場地B", "場地C", "場地D"]
    assert snapshot.config["event"]["event_dates"] == ["2026-10-24", "2026-10-25"]
    assert not snapshot.results


@pytest.mark.parametrize("count", [7, 8, 9, 11, 12, 13, 14, 16, 19])
def test_demo_pool_positions_partition_all_qualifiers_without_invented_entries(count):
    plan = DEMO.format_plan(count)
    assert sum(plan["pool_sizes"]) == count
    assert set(plan["pool_sizes"]) <= {3, 4}
    assert plan["advancement_counts"] == [2] * len(plan["pool_sizes"])
    positions = plan["qualifier_positions"]
    assert len(positions) == plan["bracket_capacity"]
    assert sorted(i for i in positions if i is not None) == list(
        range(sum(plan["advancement_counts"]))
    )
