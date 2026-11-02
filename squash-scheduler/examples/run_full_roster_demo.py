"""Schedule all 147 Word registrations on four confirmed courts as a NEW DEMO draw.

Exact-name identity sharing and court labels are user-confirmed. Formats, seed,
play/buffer/rest durations and court hours are demonstration assumptions, never
claims about the original 2026 competition. No results or release approval are created.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from squash_scheduler.domain.config import EventConfig, load_config
from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import (
    Division,
    Participant,
    Snapshot,
    canonical_hash,
    stable_id,
    utcnow,
)
from squash_scheduler.domain.registrations import resolve_roster
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis, validate
from squash_scheduler.exporters.assets import import_asset
from squash_scheduler.exporters.documents import (
    build_documents,
    export_excel,
    render_html,
    view_model,
)
from squash_scheduler.exporters.portal import write_portal
from squash_scheduler.scheduling.constraints import validate_schedule
from squash_scheduler.scheduling.solver import solve_schedule

ACTOR = "Justin-demo"
DATES = ["2026-10-24", "2026-10-25"]
SEED = 20261028
ASSUMPTIONS: dict[str, Any] = {
    "confirmed_by_user": [
        "Exact same official names across groups identify the same person",
        "Four courts: 場地A, 場地B, 場地C, 場地D",
    ],
    "source_evidence": "147 Word registrations in 17 divisions; PDF dates 2026-10-24 and 2026-10-25",
    "demo_only": {
        "draw": "A new seeded demonstration draw using the Word names, never the historical draw",
        "seed": SEED,
        "formats": "3-6 entries: round robin; larger groups: pools of 3/4, top two advance, knockout and bronze",
        "single_entry": "Retain registration and division as exhibition_only; no invented opponent or match",
        "court_hours": "08:00-22:00 Asia/Taipei on both source dates; no lunch blackout",
        "estimated_play_minutes": 10,
        "court_buffer_minutes": 5,
        "minimum_player_rest_minutes": 15,
        "start_grid_minutes": 5,
        "court_transfer_minutes": 0,
        "ranking_confirmation_minutes": 5,
        "match_rules": "Best of 3 games to 11, win by 2, no point cap; explicit demo forfeit policies",
    },
    "status": "DEMO / DRAFT: not the published 2026 draw, not a release approval",
}


def pool_sizes(count: int) -> list[int]:
    """Partition every count at least seven into three/four-person pools."""
    for fours in range(count // 4, -1, -1):
        remainder = count - fours * 4
        if remainder % 3 == 0:
            return [4] * fours + [3] * (remainder // 3)
    raise ValueError("Count cannot be partitioned into pools of three or four")


def format_plan(count: int) -> dict[str, Any]:
    sizes = pool_sizes(count) if count > 6 else []
    qualifiers = 2 * len(sizes)
    capacity = 1 << (max(2, qualifiers) - 1).bit_length()
    positions: list[int | None] = []
    byes = capacity - qualifiers
    for index in range(qualifiers):
        positions.append(index)
        if index < byes:
            positions.append(None)
    return {
        "template_id": "pools_knockout" if sizes else "round_robin",
        "template_version": "1.0.0",
        "pool_sizes": sizes,
        "advancement_counts": [2] * len(sizes),
        "bracket_capacity": capacity,
        "placement_matches": ["bronze"] if sizes else [],
        **({"qualifier_positions": positions} if sizes else {}),
        "status": "draft",
        "approval_id": None,
    }


def build_snapshot(project_root: Path) -> Snapshot:
    """Use the source importer candidate IDs and raw references without name heuristics."""
    inventory = project_root / "output" / "historical-inventory"
    imported = json.loads((inventory / "import-report.json").read_text("utf-8"))
    evidence = json.loads((inventory / "report.json").read_text("utf-8"))
    candidates = [c for c in imported["candidates"] if c["status"] != "ignored_with_reason"]
    headings = [c for c in imported["candidates"] if c.get("ignored_reason") == "division_heading"]
    counts = Counter(c["division_id"] for c in candidates)
    if len(candidates) != 147 or len(counts) != 17:
        raise ValueError(
            "Full-roster demo requires exactly the 147 source registrations in 17 groups"
        )
    divisions = []
    for heading in headings:
        name = heading["official_name"]
        school = name.startswith(("高中", "國中", "國小"))
        divisions.append(
            Division(
                id=heading["division_id"],
                name=name,
                category="school" if school else "competitive",
                gender_category="male" if "男子" in name else "female",
                level_order=None if school else ord(name[-2]) - ord("A") + 1,
                status="exhibition_only" if counts[heading["division_id"]] == 1 else "active",
            )
        )
    config = load_config(project_root.parent / "squash-event-config.template.json")
    config["event"].update(
        id=stable_id("full-roster-demo", "event"),
        name="全名單四場地排程 DEMO（新抽籤／非2026原賽程）",
        timezone="Asia/Taipei",
        event_dates=DATES,
        venue_name="四場地示範；實際開放時段未確認",
        actor_id=ACTOR,
    )
    config.update(
        divisions_status="configured",
        divisions=[d.model_dump(mode="json") for d in divisions],
        courts_status="configured",
        merge_whitelist_status="configured",
    )
    config["courts"] = [
        {
            "id": stable_id("full-roster-demo-court", letter),
            "label": "場地" + letter,
            "sort_order": index,
            "availability": [
                {"start_at": f"{day}T08:00:00+08:00", "end_at": f"{day}T22:00:00+08:00"}
                for day in DATES
            ],
            "blackouts": [],
        }
        for index, letter in enumerate("ABCD")
    ]
    config["timing_defaults"] = {
        k: ASSUMPTIONS["demo_only"][k]
        for k in (
            "estimated_play_minutes",
            "court_buffer_minutes",
            "minimum_player_rest_minutes",
            "start_grid_minutes",
            "court_transfer_minutes",
            "ranking_confirmation_minutes",
        )
    }
    config["timing_defaults"]["daily_grid_anchors"] = [
        {"date": day, "local_time": "08:00"} for day in DATES
    ]
    config["draw_defaults"].update(
        seeded_entries_mode="none", affiliation_avoidance_mode="disabled"
    )
    config["publication"].update(
        layout_template_id="standard-v1",
        paper_size="A4",
        orientation="landscape",
        qr_mode="disabled",
        sponsor_mode="none",
        font_asset_id=None,
    )
    config["match_rule_defaults"] = {
        "best_of_games": 3,
        "points_to_win_game": 11,
        "win_by_points": 2,
        "point_cap_mode": "none",
        "point_cap": None,
        "late_arrival_rule": {"grace_minutes": 5, "action": "manual_review", "demo_only": True},
        "walkover_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "winner_points": 2,
            "loser_points": 0,
            "demo_only": True,
        },
        "retirement_rule": {
            "include_in_rankings": True,
            "counts_as_played": True,
            "winner_points": 2,
            "loser_points": 0,
            "demo_only": True,
        },
        "double_forfeit_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "side_a_points": 0,
            "side_b_points": 0,
            "demo_only": True,
        },
        "replacement_rule": {"mode": "manual_review", "automatic": False, "demo_only": True},
        "ranking_denominator_rule": {"walkover": True, "retired": True, "double_forfeit": True},
        "multiway_tie_rule": {"mode": "manual_review", "demo_only": True},
    }
    config = EventConfig.model_validate(config).model_dump(mode="json")
    # Exact source spelling is the whole identity key, as explicitly confirmed by
    # Justin. No alias, whitespace, surname or similarity matching is performed.
    names = dict.fromkeys(c["official_name"] for c in candidates)
    persons = {
        name: Participant(
            id=stable_id("user-confirmed-exact-name", name), official_name=name, display_name=name
        )
        for name in names
    }
    batch = copy.deepcopy(imported)
    batch["id"] = imported["batch_id"]
    snapshot = Snapshot(
        config=config, divisions=divisions, participants=list(persons.values()), imports=[batch]
    )
    snapshot.sources = [
        {
            "id": imported["source_file_id"],
            "original_name": "Word roster (converted)",
            "sha256": evidence["word"]["converted_sha256"],
            "original_sha256": evidence["word"]["source_sha256"],
            "source_report": str(inventory / "report.json"),
        }
    ]
    patch = {
        "base_revision_id": snapshot.id,
        "batch_id": batch["id"],
        "actor_id": ACTOR,
        "reason": "Justin confirmed exact same names across divisions identify one person; retain every original registration and raw reference",
        "resolutions": [
            {"candidate_id": c["id"], "participant_id": persons[c["official_name"]].id}
            for c in candidates
        ],
    }
    resolve_roster(snapshot, patch)
    snapshot.changes.append(
        {
            "id": stable_id("full-roster-demo", "identity-decision"),
            "actor_id": ACTOR,
            "created_at": utcnow().isoformat(),
            "reason": patch["reason"],
            "type": "confirmed_identity_resolution",
            "source_candidate_count": len(candidates),
            "source_candidates_hash": canonical_hash(candidates),
            "resolutions": patch["resolutions"],
            "assumptions": copy.deepcopy(ASSUMPTIONS),
        }
    )
    for division in snapshot.divisions:
        count = counts[division.id]
        if count == 1:
            continue
        division.format_plan = format_plan(count)
    for division in snapshot.divisions:
        if division.status == "exhibition_only":
            continue
        snapshot, _ = approve(
            snapshot,
            "format:" + division.id,
            target_basis(division.format_plan),
            ACTOR,
            "DEMO format decision only; not the original 2026 competition format",
        )
    for division in snapshot.divisions:
        if division.status == "exhibition_only":
            continue
        draw = run_draw(
            snapshot,
            division.id,
            SEED,
            ACTOR,
            "DEMO new draw with real source names; not a historical reconstruction",
        )
        snapshot, _ = approve(
            snapshot,
            "draw:" + draw["id"],
            target_basis(draw),
            ACTOR,
            "Approve this saved NEW DEMO draw only, never the historical 2026 draw",
        )
    generate_matches(snapshot)
    for index, match in enumerate(snapshot.matches, 1):
        match.display_code = f"DEMO-{index:03}"
    return snapshot


def run_full_roster_demo(
    output: Path, *, time_limit: float = 60, pdf: bool = True
) -> dict[str, Any]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    snapshot = build_snapshot(root)

    def write(name: str, value: Any) -> None:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")

    write("assumptions.json", ASSUMPTIONS)
    write("snapshot-before-solve.json", snapshot.model_dump(mode="json"))
    solver = solve_schedule(snapshot, time_limit_seconds=time_limit)
    write("solver-report.json", solver)
    g4 = validate(snapshot, "G4")
    independent = [i.model_dump(mode="json") for i in validate_schedule(snapshot)]
    write("g4-report.json", {**g4, "independent_schedule_issues": independent})
    write("snapshot.json", snapshot.model_dump(mode="json"))
    report: dict[str, Any] = {
        "status": "draft_demo",
        "scope": ASSUMPTIONS["status"],
        "actor_id": ACTOR,
        "source_registrations": len(snapshot.registrations),
        "entries": len(snapshot.entries),
        "distinct_participants": len(snapshot.participants),
        "divisions": len(snapshot.divisions),
        "court_count": len(snapshot.config["courts"]),
        "court_labels": [c["label"] for c in snapshot.config["courts"]],
        "matches": len(snapshot.matches),
        "assigned_matches": len(snapshot.assignments),
        "solver_status": solver["status"],
        "g4_passed": g4["passed"],
        "independent_schedule_valid": not any(i["severity"] == "error" for i in independent),
        "assumptions": ASSUMPTIONS,
        "output": str(output),
        "single_entry_divisions": [
            d.name for d in snapshot.divisions if d.status == "exhibition_only"
        ],
        "division_summary": [
            {
                "id": d.id,
                "name": d.name,
                "category": d.category,
                "status": d.status,
                "entries": sum(e.effective_division_id == d.id for e in snapshot.entries),
                "matches": sum(m.division_id == d.id for m in snapshot.matches),
                "format": d.format_plan,
            }
            for d in snapshot.divisions
        ],
    }
    write("report.json", report)
    if solver["status"] not in {"FEASIBLE", "OPTIMAL"} or not g4["passed"]:
        report["status"] = "schedule_unresolved"
        write("report.json", report)
        return report
    if pdf:
        font = import_asset(output, Path("C:/Windows/Fonts/msjh.ttc"), "font")
        snapshot.config["publication"]["font_asset_id"] = font["asset_id"]
        write("snapshot.json", snapshot.model_dump(mode="json"))
        manifest = build_documents(
            snapshot, output / "draft", draft=True, asset_root=output / "assets"
        )
        report["document_qa_passed"] = manifest["qa"]["passed"]
        report["visual_review"] = manifest["qa"]["visual_review"]
        report["page_count"] = len(manifest["qa"].get("pages", []))
    else:
        destination = output / "draft"
        destination.mkdir()
        model = view_model(snapshot, draft=True)
        (destination / "review.html").write_text(render_html(model, destination), "utf-8")
        export_excel(snapshot, model, destination / "schedule.xlsx")
        write_portal(model, destination)
        write("render-model.json", model)
    report["draft_path"] = str(output / "draft")
    write("report.json", report)
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/full-roster-simulation"))
    parser.add_argument("--time-limit", type=float, default=60)
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run_full_roster_demo(args.output, time_limit=args.time_limit, pdf=not args.no_pdf),
            ensure_ascii=False,
            indent=2,
        )
    )
