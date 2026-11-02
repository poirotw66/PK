"""Save reusable anonymous snapshots from a completed synthetic nine-person demo."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Snapshot, new_id, stable_id
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis, validate


def create_fixtures(source: Path, destination: Path) -> None:
    nine = Snapshot.model_validate_json(source.read_text("utf-8-sig"))
    generate_matches(nine)
    assert validate(nine, "G4")["passed"]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "nine-entries.json").write_text(nine.model_dump_json(indent=2), "utf-8")
    five = nine.model_copy(deep=True)
    five.id = stable_id("synthetic-fixture", "five-round-robin")
    five.parent_id = None
    five.entries = five.entries[:5]
    registration_ids = {e.registration_id for e in five.entries}
    five.registrations = [r for r in five.registrations if r.id in registration_ids]
    people = {r.participant_id for r in five.registrations}
    five.participants = [p for p in five.participants if p.id in people]
    five.stages, five.pools, five.matches, five.assignments = [], [], [], []
    five.draws, five.exclusions, five.results, five.approvals = [], [], [], []
    five.changes, five.imports, five.issues, five.ranking_decisions = [], [], [], []
    raw = "\n".join(p.official_name for p in five.participants)
    source_id = stable_id("synthetic-fixture", "five-roster-source")
    five.sources = [
        {
            "id": source_id,
            "original_name": "five.synthetic.txt",
            "media_type": "text/plain",
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "imported_at": five.created_at.isoformat(),
            "fixture_only": True,
        }
    ]
    for index, reg in enumerate(five.registrations):
        person = next(p for p in five.participants if p.id == reg.participant_id)
        reg.source_ref = {
            "source_file_id": source_id,
            "paragraph_index": index,
            "raw_text": person.official_name,
            "page_number": None,
        }
    division = five.divisions[0]
    division.format_plan = {
        "template_id": "round_robin",
        "template_version": "1.0.0",
        "pool_sizes": [],
        "advancement_counts": [],
        "bracket_capacity": None,
        "placement_matches": [],
        "status": "draft",
        "approval_id": None,
    }
    five.config["event"]["name"] = "匿名五人循環例外（合成資料）"
    five.config["event"]["id"] = stable_id("synthetic-fixture", "five-event")
    five.config["divisions"] = [division.model_dump(mode="json")]
    five, _ = approve(
        five,
        f"format:{division.id}",
        target_basis(division.format_plan),
        "synthetic-reviewer",
        "Synthetic fixture format only",
    )
    generate_matches(five)
    exclusion = {
        "id": new_id(),
        "match_id": five.matches[0].id,
        "reason": "Synthetic approved omission; manual ranking required",
        "status": "draft",
        "approval_id": None,
    }
    five.exclusions.append(exclusion)
    five, _ = approve(
        five,
        f"exclusion:{exclusion['id']}",
        target_basis(exclusion),
        "synthetic-reviewer",
        "Synthetic fixture exclusion only",
    )
    generate_matches(five)
    assert validate(five, "G3")["passed"]
    (destination / "five-round-robin-exclusion.json").write_text(
        five.model_dump_json(indent=2), "utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("tests/fixtures/synthetic"))
    args = parser.parse_args()
    create_fixtures(args.source, args.output)
