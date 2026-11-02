"""Build an entirely fictional eight-player domain snapshot and publication.

Run with the project Python. Each invocation requires a fresh output directory;
use --output output/consolation-demo-2 for another independent run.
No historical loser source, real-event term or release readiness is inferred.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from itertools import combinations
from pathlib import Path

from docx import Document

from squash_scheduler.domain.matches import dependency_map, possible_participants
from squash_scheduler.domain.models import Snapshot, stable_id
from squash_scheduler.scheduling.constraints import validate_schedule
from squash_scheduler.storage.repository import Repository, atomic_json

ACTOR = "fictional-consolation-operator"
NOTICE = "全虛構鼓勵賽驗證；不核准真實賽事，也不確認歷史原稿敗者來源。"


def synthetic_config() -> dict:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root.parent / "squash-event-config.template.json").read_text("utf-8-sig"))
    division_id = stable_id("fictional-consolation", "division")
    config.update(document_kind="event_config", configuration_status="complete")
    config["event"] = {
        "id": stable_id("fictional-consolation", "event"),
        "name": "全虛構八人鼓勵賽驗證",
        "timezone": "Asia/Taipei",
        "event_dates": ["2030-01-01"],
        "venue_name": "虛構測試館",
        "actor_id": ACTOR,
    }
    config["divisions_status"] = config["courts_status"] = "configured"
    config["merge_whitelist_status"] = "configured"
    config["divisions"] = [
        {
            "id": division_id,
            "name": "國中男子組",
            "category": "school",
            "gender_category": "male",
            "status": "active",
            "minimum_entries": 3,
            "format_plan": {
                "template_id": "single_elimination",
                "template_version": "1.0.0",
                "pool_sizes": [],
                "advancement_counts": [],
                "bracket_capacity": 8,
                "placement_matches": ["bronze"],
                "status": "draft",
                "approval_id": None,
            },
            "rule_overrides": {},
            "draw_overrides": {},
        }
    ]
    config["registration"]["division_aliases"] = {"國中男子組": division_id}
    config["courts"] = [
        {
            "id": stable_id("fictional-consolation", "court-" + label),
            "label": label,
            "sort_order": i,
            "availability": [
                {"start_at": "2030-01-01T08:00:00+08:00", "end_at": "2030-01-01T18:00:00+08:00"}
            ],
            "blackouts": [
                {"start_at": "2030-01-01T12:00:00+08:00", "end_at": "2030-01-01T13:00:00+08:00"}
            ],
        }
        for i, label in enumerate("ABCD")
    ]
    config["timing_defaults"] = {
        "start_grid_minutes": 15,
        "daily_grid_anchors": [{"date": "2030-01-01", "local_time": "08:00"}],
        "estimated_play_minutes": 25,
        "court_buffer_minutes": 5,
        "minimum_player_rest_minutes": 20,
        "court_transfer_minutes": 5,
        "ranking_confirmation_minutes": 10,
    }
    config["match_rule_defaults"] = {
        "best_of_games": 3,
        "points_to_win_game": 11,
        "win_by_points": 2,
        "point_cap_mode": "none",
        "point_cap": None,
        "late_arrival_rule": "遲到五分鐘由虛構裁判判棄權。",
        "walkover_rule": "棄權勝方晉級，不計實際出賽。",
        "retirement_rule": "退賽方判負，對手晉級，計實際出賽。",
        "double_forfeit_rule": "雙方棄權則該席無人晉級，由虛構裁判記錄。",
        "replacement_rule": "抽籤後不補位。",
        "ranking_denominator_rule": "僅計實際出賽；棄權及雙棄權不計。",
        "multiway_tie_rule": "本合成淘汰賽不設同分循環排名。",
    }
    config["draw_defaults"].update(
        seeded_entries_mode="none", affiliation_avoidance_mode="disabled"
    )
    config["publication"].update(
        layout_template_id="reference-v2",
        paper_size="A4",
        orientation="landscape",
        document_profile="prospectus",
        qr_mode="disabled",
        sponsor_mode="none",
        draft_note=NOTICE,
        prospectus={
            "approval_status": "approved",
            "organizers": "虛構排程驗證小組；所有條文只適用本合成案例。",
            "co_organizers": "無協辦單位。",
            "eligibility": "八位虛構國中男子組非種子選手；無真實身份或真實報名。",
            "registration": "八名測試選手由腳本建立，免費且不接受外部報名。",
            "check_in": "2030年1月1日07:45於虛構測試館報到。",
            "competition": [
                "八人單淘汰主線七場，準決賽敗者另打一場季軍賽。",
                "鼓勵賽僅限國中男、女組的非種子球員，且須為籤表第一輪實際出賽敗者；首輪輪空、棄權或未到場者不得參加。",
                "僅本合成案例：八人皆非種子、無輪空，主線第一輪四場敗者依籤序兩兩進鼓勵準決賽，勝者打鼓勵決賽，共三場兩輪；此對位並非真實賽事對位。",
                "四場地A至D，08:00至18:00；12:00至13:00停賽。",
                "每15分鐘起賽格點，預估打25分鐘，場地緩衝5分鐘；球員休息20分鐘，換場另加5分鐘。",
                NOTICE,
            ],
            "awards": "主賽冠亞季軍及鼓勵賽冠軍授予虛構紀念證明，無獎金。",
            "appeals": "賽後十分鐘內向虛構裁判以文字提出，由測試小組裁決。",
            "contact": "fictional@example.test；示範專用信箱，不寄送通知。",
        },
    )
    config["historical_review"] = {
        "scope": "fictional_consolation_only",
        "original_attachments_revalidated": False,
        "unresolved_items": [],
        "notice": NOTICE,
    }
    return config


def audit_rows(snapshot: Snapshot) -> dict:
    """Expose typed sides, entry candidates and all potential shared-player gaps."""
    dependencies = dependency_map(snapshot)
    candidates = possible_participants(snapshot)
    assignments = {a.match_id: a for a in snapshot.assignments}
    courts = {c["id"]: c["label"] for c in snapshot.config["courts"]}
    rows = []
    for match in snapshot.matches:
        assignment = assignments[match.id]
        rows.append(
            {
                "match_id": match.id,
                "code": match.display_code,
                "kind": match.kind,
                "stage_id": match.stage_id,
                "round": match.round,
                "side_a": match.side_a.model_dump(mode="json"),
                "side_b": match.side_b.model_dump(mode="json"),
                "dependency_ids": sorted(dependencies[match.id]),
                "possible_participant_ids": sorted(candidates[match.id]),
                "court": courts[assignment.court_id],
                **assignment.model_dump(mode="json"),
            }
        )
    gaps = []
    for first, second in combinations(snapshot.matches, 2):
        shared = candidates[first.id] & candidates[second.id]
        if not shared:
            continue
        a, b = sorted([assignments[first.id], assignments[second.id]], key=lambda x: x.start_at)
        gaps.append(
            {
                "before_match_id": a.match_id,
                "after_match_id": b.match_id,
                "possible_participant_ids": sorted(shared),
                "play_end_to_start_minutes": (b.start_at - a.play_end_at).total_seconds() / 60,
                "required_minutes": 20 + (5 if a.court_id != b.court_id else 0),
            }
        )
    return {"matches": rows, "potential_shared_player_gaps": gaps}


def build(output: Path, font: Path, *, documents: bool = True) -> dict:
    output, font = output.resolve(), font.resolve()
    if not font.is_file():
        raise FileNotFoundError("Supply an existing controlled local Chinese font with --font")
    output.mkdir(parents=True, exist_ok=False)
    config = synthetic_config()
    config_path = output / "event.synthetic.json"
    atomic_json(config_path, config)
    document = Document()
    document.add_paragraph("國中男子組")
    for i in range(1, 9):
        document.add_paragraph(f"{i}. 虛構選手{i:02}")
    roster = output / "roster.synthetic.docx"
    document.save(roster)
    workspace = output / "tournament"
    commands = []

    def cli(*args: str) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "squash_scheduler",
                *args,
                "--workspace",
                str(workspace),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        if completed.returncode:
            raise RuntimeError(f"CLI {args}: {completed.stdout}\n{completed.stderr}")
        commands.append({"args": list(args), "exit_code": completed.returncode})
        return json.loads(completed.stdout)

    cli("init", "--config", str(config_path))
    cli("roster", "import", "--file", str(roster))
    repo = Repository(workspace)
    current = repo.load()
    patch_path = output / "identity.synthetic.json"
    atomic_json(
        patch_path,
        {
            "base_revision_id": current.id,
            "batch_id": current.imports[0]["id"],
            "actor_id": ACTOR,
            "reason": NOTICE,
            "resolutions": [
                {
                    "candidate_id": c["id"],
                    "create_participant": {
                        "official_name": c["official_name"],
                        "display_name": c["official_name"],
                    },
                }
                for c in current.imports[0]["candidates"]
                if c["status"] != "ignored_with_reason"
            ],
        },
    )
    cli("roster", "resolve", "--patch", str(patch_path))
    asset = cli("assets", "import", "--kind", "font", "--file", str(font))
    current = repo.load()
    publication = {**current.config["publication"], "font_asset_id": asset["asset_id"]}
    patch_path = output / "font.synthetic.json"
    atomic_json(
        patch_path,
        {
            "base_revision_id": current.id,
            "actor_id": ACTOR,
            "reason": NOTICE,
            "operations": [{"op": "set_config", "field": "publication", "value": publication}],
        },
    )
    cli("changes", "apply", "--file", str(patch_path))

    def approve_target(prefix: str) -> dict:
        review = cli("review", "build")
        target = next(t for t in review["approval_targets"] if t["target"].startswith(prefix))
        return cli(
            "review",
            "approve",
            "--target",
            target["target"],
            "--hash",
            target["hash"],
            "--reason",
            NOTICE,
        )

    approve_target("format:")
    cli("draw", "run", "--division", config["divisions"][0]["id"], "--seed", "20300101")
    approve_target("draw:")
    cli("matches", "generate")
    # Real CLI workflow: preserve the already scheduled first round, import valid
    # fictional scores, review a draw proposal, then explicitly confirm its hash.
    cli("schedule", "solve", "--time-limit", "20")
    current = repo.load()
    first_round = sorted(
        (m for m in current.matches if m.kind == "competitive" and m.round == 1),
        key=lambda m: m.bracket_position,
    )
    results_path = output / "first-round.synthetic.json"
    atomic_json(
        results_path,
        {
            "results": [
                {
                    "match_id": m.id,
                    "outcome": "played",
                    "status": "confirmed",
                    "winner_id": m.side_a.entry_id,
                    "games": [{"side_a": 11, "side_b": 4}, {"side_a": 11, "side_b": 4}],
                }
                for m in first_round
            ]
        },
    )
    cli("results", "import", "--file", str(results_path))
    proposal_path = output / "encouragement-proposal.json"
    proposed = cli(
        "encouragement",
        "propose",
        "--division",
        config["divisions"][0]["id"],
        "--seed",
        "20300102",
        "--reason",
        NOTICE,
        "--output",
        str(proposal_path),
    )
    cli(
        "encouragement",
        "apply",
        "--file",
        str(proposal_path),
        "--hash",
        proposed["proposal"]["proposal_hash"],
        "--reason",
        NOTICE,
    )
    solved = cli("schedule", "solve", "--time-limit", "20")
    validation = cli("validate", "--gate", "G4")
    independent = [i.model_dump(mode="json") for i in validate_schedule(repo.load())]
    if any(i["severity"] == "error" for i in independent):
        raise RuntimeError("Independent schedule validation failed")
    cli("validate", "--gate", "G5")
    approve_target("revision:")
    snapshot = repo.load()
    atomic_json(output / "snapshot.json", snapshot.model_dump(mode="json"))
    atomic_json(output / "match-audit.json", audit_rows(snapshot))
    atomic_json(output / "independent-validation.json", {"issues": independent})
    manifest = None
    if documents:
        from squash_scheduler.exporters.documents import build_documents

        manifest = build_documents(snapshot, output / "booklet", asset_root=workspace / "assets")
    report = {
        "synthetic_only": True,
        "notice": NOTICE,
        "historical_sources_confirmed": False,
        "real_event_approved": False,
        "visual_review": "required",
        "approved_synthetic_revision_id": snapshot.id,
        "participants": len(snapshot.participants),
        "matches": len(snapshot.matches),
        "encouragement_matches": 3,
        "encouragement_rounds": 2,
        "court_labels": list("ABCD"),
        "solver_status": solved["status"],
        "g4_passed": validation["passed"],
        "independent_schedule_valid": True,
        "document_qa_passed": manifest["qa"]["passed"] if manifest else None,
        "workspace": str(workspace),
        "booklet": str(output / "booklet") if documents else None,
        "commands": commands,
    }
    atomic_json(output / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/consolation-demo"))
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msjh.ttc"))
    parser.add_argument("--no-documents", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.output, args.font, documents=not args.no_documents),
            ensure_ascii=True,
            indent=2,
        )
    )
