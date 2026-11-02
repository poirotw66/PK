"""Measure a synthetic 300-player / 500-match workload, without claiming an SLA.

Run: python examples/benchmark.py --output output/benchmark --time-limit 60
Add --pdf to measure the installed PDF renderer as well as HTML and Excel.
All dates, identities, rules, approvals and courts are explicit synthetic inputs.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import sys
import time
import tracemalloc
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ortools
from docx import Document

from squash_scheduler.domain.config import EventConfig
from squash_scheduler.domain.matches import generate_matches
from squash_scheduler.domain.models import Division, EntrySide, Match, Snapshot, Stage, stable_id
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis, validate
from squash_scheduler.exporters.documents import export_excel, render_html, render_pdf, view_model
from squash_scheduler.importers.word import import_roster, resolve_roster
from squash_scheduler.scheduling import solve_schedule, validate_schedule


def _hardware() -> dict[str, Any]:
    values: dict[str, Any] = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "logical_cpu_count": os.cpu_count(),
        "python": sys.version,
        "ortools": ortools.__version__,
        "physical_ram_bytes": None,
    }
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (key, ctypes.c_uint64)
                for key in (
                    "total_physical",
                    "available_physical",
                    "total_pagefile",
                    "available_pagefile",
                    "total_virtual",
                    "available_virtual",
                    "available_extended_virtual",
                )
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            values["physical_ram_bytes"] = status.total_physical
    elif hasattr(os, "sysconf"):
        values["physical_ram_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return values


def _peak_working_set() -> int | None:
    """OS peak includes native CP-SAT allocations, which tracemalloc excludes."""
    if os.name != "nt":
        return None

    class ProcessMemory(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("page_fault_count", ctypes.c_uint32)] + [
            (key, ctypes.c_size_t)
            for key in (
                "peak_working_set",
                "working_set",
                "peak_paged_pool",
                "paged_pool",
                "peak_nonpaged_pool",
                "nonpaged_pool",
                "pagefile_usage",
                "peak_pagefile_usage",
            )
        ]

    counters = ProcessMemory()
    counters.size = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.restype = ctypes.c_void_p
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemory), ctypes.c_uint32]
    if get_memory(current_process(), ctypes.byref(counters), counters.size):
        return int(counters.peak_working_set)
    return None


def _config(division_count: int) -> dict[str, Any]:
    template = Path(__file__).resolve().parents[2] / "squash-event-config.template.json"
    config = json.loads(template.read_text("utf-8-sig"))
    dates = [(datetime(2030, 1, 1) + timedelta(days=i)).date().isoformat() for i in range(3)]
    divisions = [
        Division(
            id=stable_id("benchmark-division", str(i)),
            name=f"Synthetic Division {i + 1:03}",
            category="competitive",
            gender_category="open",
            status="active",
            format_plan={
                "template_id": "round_robin",
                "template_version": "1.0.0",
                "pool_sizes": [],
                "advancement_counts": [],
                "bracket_capacity": 4,
                "placement_matches": [],
                "status": "draft",
                "approval_id": None,
            },
        )
        for i in range(division_count)
    ]
    config.update(
        document_kind="event_config",
        configuration_status="complete",
        divisions_status="configured",
        courts_status="configured",
        merge_whitelist_status="configured",
    )
    config["event"] = {
        "id": stable_id("benchmark", "event"),
        "name": "Synthetic capacity benchmark",
        "timezone": "Asia/Taipei",
        "event_dates": dates,
        "venue_name": "Synthetic venue",
        "actor_id": "synthetic-benchmark-operator",
    }
    config["divisions"] = [d.model_dump(mode="json") for d in divisions]
    config["courts"] = [
        {
            "id": stable_id("benchmark-court", str(i)),
            "label": f"Synthetic Court {i + 1}",
            "sort_order": i,
            "availability": [
                {"start_at": f"{day}T08:00:00+08:00", "end_at": f"{day}T20:00:00+08:00"}
                for day in dates
            ],
            "blackouts": [],
        }
        for i in range(8)
    ]
    config["timing_defaults"] = {
        "start_grid_minutes": 15,
        "daily_grid_anchors": [{"date": day, "local_time": "08:00"} for day in dates],
        "estimated_play_minutes": 25,
        "court_buffer_minutes": 5,
        "minimum_player_rest_minutes": 20,
        "court_transfer_minutes": 0,
        "ranking_confirmation_minutes": 10,
    }
    config["match_rule_defaults"] = {
        "best_of_games": 3,
        "points_to_win_game": 11,
        "win_by_points": 2,
        "point_cap_mode": "none",
        "point_cap": None,
        "late_arrival_rule": "synthetic_manual_review",
        "walkover_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "winner_points": 2,
            "loser_points": 0,
        },
        "retirement_rule": {
            "include_in_rankings": True,
            "counts_as_played": True,
            "winner_points": 2,
            "loser_points": 0,
        },
        "double_forfeit_rule": {
            "include_in_rankings": True,
            "counts_as_played": False,
            "side_a_points": 0,
            "side_b_points": 0,
        },
        "replacement_rule": "synthetic_manual_review",
        "ranking_denominator_rule": {
            "played": True,
            "walkover": True,
            "retired": True,
            "double_forfeit": True,
        },
        "multiway_tie_rule": "manual_review",
    }
    config["draw_defaults"].update(
        seeded_entries_mode="none",
        affiliation_avoidance_mode="disabled",
        bye_allocation_rule="not_applicable_round_robin",
        special_slot_rules=[],
    )
    config["publication"].update(
        layout_template_id="draft-v1",
        paper_size="A4",
        orientation="landscape",
        font_asset_id=None,
        qr_mode="disabled",
        sponsor_mode="none",
    )
    config["runtime"]["solver_time_limit_seconds"] = 60
    return EventConfig.model_validate(config).model_dump(mode="json")


def create_fixture(output: Path, *, division_count: int = 100) -> tuple[Snapshot, dict[str, Any]]:
    """Actually import Word identities and create five real matches per division."""
    if division_count < 1:
        raise ValueError("division_count must be positive")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    config = _config(division_count)
    snapshot = Snapshot(
        config=config, divisions=[Division.model_validate(d) for d in config["divisions"]]
    )
    document = Document()
    for index, division in enumerate(snapshot.divisions):
        document.add_paragraph(division.name)
        for offset in range(3):
            document.add_paragraph(f"{offset + 1}. Synthetic Player {3 * index + offset + 1:03}")
    roster_path = output / "roster.synthetic.docx"
    document.save(str(roster_path))
    prepare_seconds = time.perf_counter() - started
    started = time.perf_counter()
    imported = import_roster(snapshot, roster_path)
    batch = snapshot.imports[-1]
    resolutions = [
        {
            "candidate_id": candidate["id"],
            "create_participant": {
                "official_name": candidate["official_name"],
                "display_name": candidate["official_name"],
            },
        }
        for candidate in batch["candidates"]
        if candidate["status"] != "ignored_with_reason"
    ]
    resolved = resolve_roster(
        snapshot,
        {
            "batch_id": batch["id"],
            "actor_id": "synthetic-benchmark-operator",
            "reason": "Explicit synthetic identity creation for capacity measurement",
            "resolutions": resolutions,
        },
    )
    import_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for division_id in [d.id for d in snapshot.divisions]:
        plan = next(d.format_plan for d in snapshot.divisions if d.id == division_id)
        assert plan is not None
        snapshot, _ = approve(
            snapshot,
            f"format:{division_id}",
            target_basis(plan),
            "synthetic-benchmark-operator",
            "Synthetic round-robin template reviewed",
        )
    generate_matches(snapshot)
    for index, division in enumerate(snapshot.divisions):
        entries = sorted(e.id for e in snapshot.entries if e.effective_division_id == division.id)
        stage = Stage(
            id=stable_id("benchmark-exhibition-stage", division.id),
            division_id=division.id,
            type="placement",
            order=1,
        )
        snapshot.stages.append(stage)
        for pair_index, (first, second) in enumerate(
            ((entries[0], entries[1]), (entries[1], entries[2]))
        ):
            snapshot.matches.append(
                Match(
                    id=stable_id("benchmark-exhibition", division.id, str(pair_index)),
                    division_id=division.id,
                    stage_id=stage.id,
                    kind="exhibition",
                    side_a=EntrySide(entry_id=first),
                    side_b=EntrySide(entry_id=second),
                    bracket_position=pair_index,
                    display_code=f"B{index + 1:03}-E{pair_index + 1}",
                )
            )
    from squash_scheduler.domain.rules import refresh_rule_snapshots

    refresh_rule_snapshots(snapshot)
    snapshot.config["divisions"] = [d.model_dump(mode="json") for d in snapshot.divisions]
    fixture_seconds = time.perf_counter() - started
    appearances = Counter(
        side.entry_id
        for match in snapshot.matches
        for side in (match.side_a, match.side_b)
        if isinstance(side, EntrySide)
    )
    return snapshot, {
        "prepare_word_seconds": prepare_seconds,
        "word_import_and_identity_resolution_seconds": import_seconds,
        "approve_and_generate_matches_seconds": fixture_seconds,
        "import_report": imported,
        "resolution_report": resolved,
        "appearance_distribution": dict(sorted(Counter(appearances.values()).items())),
    }


def run_benchmark(
    output: Path, *, time_limit_seconds: float = 60, pdf: bool = False
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "synthetic_only": True,
        "measurement_status": "running",
        "generated_at": datetime.now(UTC).isoformat(),
        "hardware": _hardware(),
        "solver_time_limit_seconds": time_limit_seconds,
        "pdf_requested": pdf,
        "scope": "300 people, 100 divisions, 300 round-robin matches and 200 exhibitions; 8 courts, 3 dates",
        "sla_claimed": False,
        "timings": {},
    }
    report_path = output / "report.json"
    started, cpu_started = time.perf_counter(), time.process_time()
    tracemalloc.start(1)

    def save() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    def progress(stage: str) -> None:
        print(
            json.dumps(
                {"stage": stage, "elapsed_seconds": round(time.perf_counter() - started, 3)}
            ),
            flush=True,
        )
        save()

    try:
        progress("preparing_and_importing_word")
        snapshot, fixture_report = create_fixture(output)
        report["fixture"] = fixture_report
        report["counts"] = {
            "participants": len(snapshot.participants),
            "entries": len(snapshot.entries),
            "divisions": len(snapshot.divisions),
            "matches": len(snapshot.matches),
            "competitive_matches": sum(m.kind == "competitive" for m in snapshot.matches),
            "exhibition_matches": sum(m.kind == "exhibition" for m in snapshot.matches),
            "courts": len(snapshot.config["courts"]),
            "dates": len(snapshot.config["event"]["event_dates"]),
        }
        mark = time.perf_counter()
        before = validate(snapshot, "G3")
        report["timings"]["validate_g3_seconds"] = time.perf_counter() - mark
        report["pre_solve_validation"] = before
        if not before["passed"]:
            raise ValueError("Benchmark fixture failed G3; solver was not invoked")
        progress("solving")
        mark = time.perf_counter()
        solved = solve_schedule(snapshot, time_limit_seconds=time_limit_seconds)
        report["timings"]["solve_including_model_build_and_independent_validation_seconds"] = (
            time.perf_counter() - mark
        )
        report["solver_status"] = solved["status"]
        report["solver_search_wall_time_seconds"] = solved["diagnostics"].get("wall_time_seconds")
        report["solver_parameters"] = solved["diagnostics"].get("solver_parameters")
        (output / "solver-report.json").write_text(
            json.dumps(solved, ensure_ascii=False, indent=2), "utf-8"
        )
        progress("independent_validation")
        mark = time.perf_counter()
        checked = validate(snapshot, "G4")
        independent = validate_schedule(snapshot)
        report["timings"]["validate_g4_and_independent_seconds"] = time.perf_counter() - mark
        report["post_solve_validation"] = checked
        report["independent_schedule_errors"] = [
            i.model_dump(mode="json") for i in independent if i.severity == "error"
        ]
        report["validated_feasible"] = (
            solved["status"] in {"FEASIBLE", "OPTIMAL"}
            and checked["passed"]
            and not report["independent_schedule_errors"]
        )
        progress("rendering_draft_html_and_excel")
        mark = time.perf_counter()
        rendered = output / "rendered"
        rendered.mkdir()
        model = view_model(snapshot)
        html = render_html(model, rendered)
        (rendered / "review.html").write_text(html, "utf-8")
        export_excel(snapshot, model, rendered / "schedule.xlsx")
        report["timings"]["render_model_html_excel_seconds"] = time.perf_counter() - mark
        report["render_status"] = (
            "scheduled_draft" if report["validated_feasible"] else "unscheduled_draft"
        )
        if pdf:
            progress("rendering_pdf")
            mark = time.perf_counter()
            report["pdf_renderer"] = render_pdf(html, rendered / "booklet.pdf")
            report["timings"]["render_pdf_seconds"] = time.perf_counter() - mark
        (output / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), "utf-8")
        report["measurement_status"] = "completed"
    except Exception as exc:
        report["measurement_status"] = "failed"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report["memory"] = {
            "python_traced_current_bytes": current,
            "python_traced_peak_bytes": peak,
            "process_peak_working_set_bytes": _peak_working_set(),
            "note": "tracemalloc excludes native solver/browser allocations; OS working-set peak covers this process only",
        }
        report["timings"]["total_wall_seconds"] = time.perf_counter() - started
        report["timings"]["process_cpu_seconds"] = time.process_time() - cpu_started
        save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/benchmark"))
    parser.add_argument("--time-limit", type=float, default=60)
    parser.add_argument("--pdf", action="store_true")
    arguments = parser.parse_args()
    summary = run_benchmark(
        arguments.output.resolve(), time_limit_seconds=arguments.time_limit, pdf=arguments.pdf
    )
    print(
        json.dumps(
            {
                key: summary.get(key)
                for key in (
                    "measurement_status",
                    "solver_status",
                    "validated_feasible",
                    "counts",
                    "timings",
                    "memory",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
