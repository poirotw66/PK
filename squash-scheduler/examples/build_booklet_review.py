"""Rebuild the full real-roster demonstration with the reviewed booklet layout.

This keeps every source match, assignment and identity; it never repeats the draw
or solver and cannot mark demonstration data as a formally approved publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from squash_scheduler.domain.config import Prospectus
from squash_scheduler.domain.models import Snapshot, content_hash, new_id
from squash_scheduler.exporters.documents import build_documents
from squash_scheduler.exporters.prospectus import publication_content_issues
from squash_scheduler.scheduling.constraints import validate_schedule
from squash_scheduler.storage.repository import atomic_json


def build(source: Path, destination: Path, prospectus_content: Path | None = None) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("Choose a fresh review output directory")
    snapshot = Snapshot.model_validate_json((source / "snapshot.json").read_text("utf-8"))
    source_hash = content_hash(snapshot)
    source_revision = snapshot.id
    original_matches = [m.model_dump(mode="json") for m in snapshot.matches]
    original_assignments = [a.model_dump(mode="json") for a in snapshot.assignments]
    source_assets = source / "assets"
    snapshot.parent_id, snapshot.id, snapshot.status = snapshot.id, new_id(), "draft"
    snapshot.config["event"]["name"] = "2026 中正盃壁球錦標賽"
    snapshot.config["publication"].update(
        layout_template_id="reference-v2",
        paper_size="A4",
        orientation="landscape",
        document_profile="prospectus",
        draft_note="四場地新編模擬；非原公布籤位與賽程",
        prospectus={"approval_status": "draft"},
    )
    if prospectus_content:
        snapshot.config["publication"]["prospectus"] = Prospectus.model_validate_json(
            prospectus_content.read_text("utf-8-sig")
        ).model_dump(mode="json")
    # Venue and rules remain unknown/demonstration; do not imply official approval.
    assert original_matches == [m.model_dump(mode="json") for m in snapshot.matches]
    assert original_assignments == [a.model_dump(mode="json") for a in snapshot.assignments]
    # This new demonstration edition uses readable chronological display numbers.
    # Match IDs, typed opponents, rules and all court/time assignments stay intact.
    assignments = {a.match_id: a for a in snapshot.assignments}
    courts = {c["id"]: c["sort_order"] for c in snapshot.config["courts"]}
    number_mapping = []
    for number, match in enumerate(
        sorted(
            snapshot.matches,
            key=lambda m: (
                assignments[m.id].start_at,
                courts[assignments[m.id].court_id],
                m.display_code or m.id,
            ),
        ),
        1,
    ):
        number_mapping.append(
            {"match_id": match.id, "source_code": match.display_code, "review_code": str(number)}
        )
        match.display_code = str(number)
    for original, current in zip(original_matches, snapshot.matches, strict=True):
        assert {k: v for k, v in original.items() if k != "display_code"} == {
            k: v for k, v in current.model_dump(mode="json").items() if k != "display_code"
        }
    errors = [
        i.model_dump(mode="json") for i in validate_schedule(snapshot) if i.severity == "error"
    ]
    if errors:
        raise ValueError("Source schedule no longer validates: " + json.dumps(errors))
    destination.mkdir(parents=True)
    atomic_json(destination / "review-snapshot.json", snapshot.model_dump(mode="json"))
    atomic_json(
        destination / "source-link.json",
        {
            "source_revision": source_revision,
            "source_content_hash": source_hash,
            "source_snapshot_sha256": hashlib.sha256(
                (source / "snapshot.json").read_bytes()
            ).hexdigest(),
            "match_relations_and_rules_preserved": True,
            "assignments_preserved": True,
            "display_number_mapping": number_mapping,
            "notice": "New print layout of the 279-match DEMO, not the original 2026 draw or formal approval",
        },
    )
    manifest = build_documents(
        snapshot, destination / "booklet", draft=True, asset_root=source_assets
    )
    # Extract a concise sample packet from the actual generated complete PDF.
    folder = destination / "booklet"
    model = json.loads((folder / "render-model.json").read_text("utf-8"))
    graph_pages = manifest["qa"]["pages"]
    with pymupdf.open(folder / "booklet.pdf") as full:
        selected = []
        for phrase in ("競賽辦法", "比賽與排名規則", "男子B組", "男子F組", "2026年10月24日"):
            wanted = 2 if phrase == "男子F組" else 1
            found = [
                i
                for i, page in enumerate(full)
                if phrase in "".join(page.get_text().split())
                and (
                    phrase != "競賽辦法"
                    or next(
                        (line.strip() for line in page.get_text().splitlines() if line.strip()), ""
                    )
                    == phrase
                )
                and (phrase not in {"男子B組", "男子F組"} or page.rect.width > page.rect.height)
            ]
            for index in found[:wanted]:
                if index not in selected:
                    selected.append(index)
        samples = pymupdf.open()
        for index in selected:
            samples.insert_pdf(full, from_page=index, to_page=index)
        samples.save(destination / "representative-pages.pdf")
        samples.close()
    report = {
        "status": "draft_review_required",
        "matches": len(model["matches"]),
        "registrations": len(model["roster"]),
        "people": len(model["participants"]),
        "court_labels": [c["label"] for c in model["courts"]],
        "page_count": len(graph_pages),
        "graph_count": len(model.get("graph_metadata", {})),
        "rule_summary_count": len(model["rule_summaries"]),
        "document_qa_passed": manifest["qa"]["passed"],
        "independent_schedule_valid": not errors,
        "sample_original_pages": [i + 1 for i in selected],
        "missing_formal_content": publication_content_issues(snapshot.config),
        "historical_draw_recreated": False,
        "formal_publication_approved": False,
    }
    atomic_json(destination / "review-report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("output/full-roster-simulation-delivery")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prospectus-content", type=Path)
    arguments = parser.parse_args()
    print(
        json.dumps(
            build(arguments.source, arguments.output, arguments.prospectus_content),
            ensure_ascii=True,
            indent=2,
        )
    )
