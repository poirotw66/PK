"""Create isolated fictional round-robin, deferred F and junior reader rehearsals."""

from __future__ import annotations

import argparse
import json
import shutil
from copy import deepcopy
from pathlib import Path

from squash_scheduler.domain.deferred_draws import bind_deferred_draw, propose_deferred_draw
from squash_scheduler.domain.draws import run_draw
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import (
    Division,
    Entry,
    Participant,
    PoolRankSide,
    Registration,
    Snapshot,
    stable_id,
)
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis, validate
from squash_scheduler.scheduling import solve_schedule
from squash_scheduler.storage.repository import Repository, atomic_json


def scenario(config: dict, key: str, count: int, name: str, template: str) -> Snapshot:
    config = deepcopy(config)
    config["event"].update(id=stable_id("reader-scenario", key), name=name, actor_id="Justin")
    config["publication"].update(
        draft_note="全虛構驗收案例，非正式賽事核准。", prospectus={"approval_status": "draft"}
    )
    config["draw_defaults"].update(
        seeded_entries_mode="none", seeded_entries=[], affiliation_avoidance_mode="disabled"
    )
    config["match_rule_defaults"]["walkover_rule"] = {
        "include_in_rankings": True,
        "counts_as_played": False,
        "winner_points": 2,
        "loser_points": 0,
    }
    config["match_rule_defaults"]["ranking_denominator_rule"] = {"walkover": True}
    for court in config["courts"]:
        if court["label"] in "ABCD":
            court["label"] = "場地" + court["label"]
    plan = {
        "template_id": template,
        "template_version": "1.0.0",
        "pool_sizes": [],
        "advancement_counts": [],
        "bracket_capacity": 1 << (count - 1).bit_length(),
        "placement_matches": ["bronze"] if template != "round_robin" else [],
        "status": "draft",
        "approval_id": None,
    }
    if template == "pools_knockout":
        plan.update(
            pool_sizes=[3, 3, 3],
            advancement_counts=[2, 2, 2],
            bracket_capacity=8,
            qualifier_positions=[0, None, 1, None, 2, 3, 4, 5],
        )
    division = Division(
        id=stable_id("reader-scenario-division", key),
        name="國中男子組" if key == "junior" else "男子 F 組" if key == "f" else "虛構循環組",
        category="school" if key == "junior" else "competitive",
        gender_category="male",
        status="active",
        format_plan=plan,
    )
    config["divisions"] = [division.model_dump(mode="json")]
    config["registration"]["division_aliases"] = {division.name: division.id}
    snapshot = Snapshot(config=config, divisions=[division])
    source_id = stable_id("reader-synthetic-source", key)
    snapshot.sources.append({"id": source_id, "synthetic_only": True})
    for index in range(1, count + 1):
        person = Participant(
            id=stable_id("reader-person", key, str(index)),
            official_name=f"虛構{key}選手{index:02}",
            display_name=f"虛構{key}選手{index:02}",
        )
        registration = Registration(
            id=stable_id("reader-registration", key, str(index)),
            participant_id=person.id,
            original_division_id=division.id,
            source_ref={"source_file_id": source_id, "synthetic_only": True},
        )
        snapshot.participants.append(person)
        snapshot.registrations.append(registration)
        snapshot.entries.append(
            Entry(
                id=stable_id("reader-entry", key, str(index)),
                registration_id=registration.id,
                effective_division_id=division.id,
            )
        )
    snapshot, _ = approve(
        snapshot,
        f"format:{division.id}",
        target_basis(division.format_plan),
        "Justin",
        "Review fictional reader scenario",
    )
    if template != "round_robin":
        draw = run_draw(snapshot, division.id, 20261029, "Justin")
        snapshot, _ = approve(
            snapshot,
            f"draw:{draw['id']}",
            target_basis(draw),
            "Justin",
            "Confirm fictional main draw",
        )
    generate_matches(snapshot)
    for index, match in enumerate(snapshot.matches, 1):
        match.display_code = f"{key.upper()}-{index:02}"
    if key == "f":
        pools = snapshot.pools
        slots = [
            {
                "slot_id": str(i),
                "candidate_sides": [
                    PoolRankSide(pool_id=p.id, rank=1 if i in {1, 3, 6} else 2).model_dump()
                    for p in pools
                ],
            }
            for i in range(1, 7)
        ]
        record = propose_deferred_draw(
            snapshot,
            {
                "division_id": division.id,
                "slots": slots,
                "procedure": {
                    "description": "虛構現場抽位；小組第一抽1、3、6，其餘為小組第二，非歷史執行紀錄。",
                    "execution_trigger": "after_sources_confirmed",
                    "duration_minutes": 5,
                    "earliest_at": min(
                        window["start_at"]
                        for court in config["courts"]
                        for window in court["availability"]
                    ),
                },
            },
            "Justin",
            "Fictional deferred F draw",
        )
        snapshot, _ = approve(
            snapshot,
            f"deferred_draw:{record['id']}",
            target_basis(record),
            "Justin",
            "Confirm fictional procedure",
        )
        bindings = [
            {"match_id": match.id, "side": key}
            for match in snapshot.matches
            for key in ("side_a", "side_b")
            if getattr(match, key).type == "pool_rank"
        ]
        bind_deferred_draw(
            snapshot,
            record["id"],
            [{**binding, "slot_id": str(i)} for i, binding in enumerate(bindings, 1)],
        )
    report = validate(snapshot, "G3")
    if not report["passed"]:
        raise ValueError(str(report))
    solved = solve_schedule(snapshot, time_limit_seconds=10)
    if solved["status"] not in {"FEASIBLE", "OPTIMAL"}:
        raise ValueError(str(solved))
    return snapshot


def build(destination: Path, source: Path) -> dict:
    if destination.exists():
        raise FileExistsError("Choose a fresh scenario output directory")
    config = Repository(source).load().config
    report = {}
    for key, count, name, template in [
        ("rr", 3, "虛構循環賽 · 即時積分演練", "round_robin"),
        ("f", 9, "虛構男子 F · 分組與現場抽位演練", "pools_knockout"),
        ("junior", 8, "虛構國中組 · 鼓勵與季軍賽演練", "single_elimination"),
    ]:
        snapshot = scenario(config, key, count, name, template)
        repo = Repository(destination / key)
        with repo.lock():
            stored = repo.save(snapshot, None)
        if (source / "assets").is_dir():
            shutil.copytree(source / "assets", repo.root / "assets")
        report[key] = {
            "workspace": str(repo.root),
            "revision_id": stored.id,
            "entries": len(stored.entries),
            "matches": len(stored.matches),
        }
    atomic_json(destination / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("output/operator-demo-final/junior"))
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.source), ensure_ascii=False, indent=2))
