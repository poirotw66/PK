"""Create a fresh, explicitly fictional, current-time operator rehearsal workspace."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Entry, Participant, Registration, new_id, utcnow
from squash_scheduler.domain.validation import validate
from squash_scheduler.operator_actions import execute_action, prepare_action
from squash_scheduler.scheduling import solve_schedule
from squash_scheduler.storage.repository import Repository, atomic_json

NOTICE = "全虛構現場演練；九名虛構選手、八個正賽席位及一名候補。非正式賽事或核准簡章。"


def build(destination: Path, source: Path, *, at: datetime | None = None) -> dict:
    destination, source = destination.resolve(), source.resolve()
    if destination.exists():
        raise FileExistsError("Choose a fresh destination; existing trials are never overwritten")
    original = Repository(source).load()
    if len(original.entries) != 8 or len(original.matches) != 8 or original.results:
        raise ValueError("Source must be the clean fictional eight-player junior operator demo")
    snapshot = original.model_copy(deep=True)
    local = (at or datetime.now(ZoneInfo("Asia/Taipei"))).astimezone(ZoneInfo("Asia/Taipei"))
    start = local.replace(minute=(local.minute // 15) * 15, second=0, microsecond=0)
    end = start + timedelta(hours=8)
    dates = sorted({start.date().isoformat(), end.date().isoformat()})
    snapshot.status = "draft"
    snapshot.config["event"].update(
        name="虛構現場演練 · 場地與候補管理",
        actor_id="Justin",
        event_dates=dates,
        venue_name="虛構四場地測試館",
    )
    snapshot.config["timing_defaults"].update(
        start_grid_minutes=15,
        daily_grid_anchors=[{"date": date, "local_time": "00:00:00"} for date in dates],
        estimated_play_minutes=25,
        court_buffer_minutes=5,
        minimum_player_rest_minutes=20,
        court_transfer_minutes=5,
    )
    for label, court in zip("ABCD", snapshot.config["courts"], strict=True):
        court.update(
            label="場地" + label,
            availability=[{"start_at": start.isoformat(), "end_at": end.isoformat()}],
            blackouts=[],
        )
    snapshot.config["match_rule_defaults"].update(
        walkover_rule={
            "include_in_rankings": True,
            "counts_as_played": False,
            "winner_points": 2,
            "loser_points": 0,
        },
        ranking_denominator_rule={"walkover": False},
        replacement_rule="僅本虛構演練：未開賽且未鎖定對戰，可經影響審核由已確認候補遞補。",
    )
    publication = snapshot.config["publication"]
    publication["draft_note"] = NOTICE
    publication["prospectus"] = {
        "approval_status": "draft",
        "organizers": "待補",
        "co_organizers": "待補",
        "eligibility": "待補",
        "registration": "待補",
        "check_in": "待補",
        "competition": [NOTICE, "演練棄權規則：對手晉級、不計實際出賽；僅供測試。"],
        "awards": "待補",
        "appeals": "待補",
        "contact": "待補",
    }
    person = Participant(official_name="虛構候補09", display_name="虛構候補09")
    registration = Registration(
        participant_id=person.id,
        original_division_id=snapshot.divisions[0].id,
        source_ref={
            "source_file_id": snapshot.sources[0]["id"],
            "synthetic": "Explicit generated reserve; not in original source roster",
        },
    )
    entry = Entry(registration_id=registration.id, effective_division_id=snapshot.divisions[0].id)
    snapshot.participants.append(person)
    snapshot.registrations.append(registration)
    snapshot.entries.append(entry)
    operation = prepare_action(
        snapshot,
        {
            "action": "reserve",
            "entry_id": entry.id,
            "actor": "Justin",
            "reason": NOTICE,
        },
    )
    snapshot, _ = execute_action(snapshot, operation)
    snapshot.assignments = []
    generate_matches(snapshot)
    for index, match in enumerate(snapshot.matches, 1):
        match.display_code = f"LIVE-{index:02}"
    solver = solve_schedule(snapshot, time_limit_seconds=15)
    if solver["status"] not in {"FEASIBLE", "OPTIMAL"}:
        raise ValueError("Synthetic live schedule is infeasible: " + str(solver))
    validation = validate(snapshot, "G4")
    if not validation["passed"]:
        raise ValueError("Synthetic live demo failed validation: " + str(validation))
    snapshot.changes.append(
        {
            "id": new_id(),
            "action": "live_demo.create",
            "actor_id": "Justin",
            "reason": NOTICE,
            "created_at": utcnow().isoformat(),
            "source_workspace": str(source),
            "source_revision_id": original.id,
            "starts_at": start.isoformat(),
        }
    )
    repository = Repository(destination)
    with repository.lock():
        stored = repository.save(snapshot, None, report=validation)
    for directory in ("assets", "source"):
        if (source / directory).is_dir():
            shutil.copytree(source / directory, destination / directory)
    result = {
        "workspace": str(destination),
        "revision_id": stored.id,
        "notice": NOTICE,
        "event_date": start.date().isoformat(),
        "timezone": "Asia/Taipei",
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "active_entries": 8,
        "reserve_entry_id": entry.id,
        "reserve_name": person.display_name,
        "matches": len(stored.matches),
        "courts": ["場地" + label for label in "ABCD"],
        "validation_passed": True,
        "note": "時間經過不會自動開賽；請在看板手動叫號、開賽。日期以建置當下台北時間計算。",
    }
    atomic_json(destination / "demo-report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/live-operator-demo"))
    parser.add_argument("--source", type=Path, default=Path("output/operator-demo-final/junior"))
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.source), ensure_ascii=True, indent=2))
