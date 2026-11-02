"""Audit the full roster and dry-run its requested policy without changing the source."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from html import escape
from pathlib import Path

from squash_scheduler.domain.config import PlayerWorkload
from squash_scheduler.domain.models import content_hash
from squash_scheduler.scheduling import solve_schedule
from squash_scheduler.scheduling.path_replay import replay_witness
from squash_scheduler.scheduling.workload import audit_workload
from squash_scheduler.storage.repository import Repository, atomic_json


def run(source: Path, output: Path, policy_path: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh output directory")
    repo = Repository(source)
    snapshot = repo.load()
    source_hash = content_hash(snapshot)
    policy = PlayerWorkload.model_validate_json(policy_path.read_text("utf-8-sig")).model_dump(
        mode="json"
    )
    report = audit_workload(snapshot, policy=policy)
    daily = [row for row in report["daily"] if row["known_exceeds_cap"]]
    rests = [row for row in report["gaps"] if row["basis"] == "known" and row["violation"]]
    proposed = snapshot.model_copy(deep=True)
    proposed.config["player_workload"] = policy
    trial_hash = content_hash(proposed)
    trial = solve_schedule(proposed, time_limit_seconds=60)
    assert proposed.config["event"]["event_dates"] == snapshot.config["event"]["event_dates"]
    if trial["status"] not in {"OPTIMAL", "FEASIBLE"}:
        assert content_hash(proposed) == trial_hash
    assert content_hash(repo.load()) == source_hash
    output.mkdir(parents=True)
    atomic_json(output / "audit.json", report)
    atomic_json(output / "solver-trial.json", trial)
    atomic_json(output / "requested-policy.json", policy)
    days = len(snapshot.config["event"]["event_dates"])
    capacity = days * policy["max_matches_per_day"]
    matches = {m.id: m for m in snapshot.matches}
    division_names = {d.id: d.name for d in snapshot.divisions}
    path_rows = sorted(
        report["path_analysis"], key=lambda row: (-row["maximum_topology_matches"], row["name"])
    )
    excessive_paths = [row for row in path_rows if row["maximum_topology_matches"] > capacity]
    for index, row in enumerate(excessive_paths, 1):
        row["constructive_replay"] = replay_witness(
            snapshot, row["participant_id"], row["paths"][0]
        )
        if index % 5 == 0:
            print(
                f"Replayed {index}/{len(excessive_paths)} overloaded qualification paths",
                flush=True,
            )
    verified_paths = [row for row in excessive_paths if row["constructive_replay"]["verified"]]
    with (output / "paths.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "選手",
                "確定場數",
                "原始候選累加",
                "相容路徑最多場數",
                "賽期可用額度",
                "超出場數",
                "路徑組別分解",
            ]
        )
        for row in path_rows:
            maximum = row["paths"][0] if row["paths"] else {"match_ids": []}
            breakdown = Counter(
                division_names[matches[mid].division_id] for mid in maximum["match_ids"]
            )
            writer.writerow(
                [
                    row["name"],
                    row["known_match_count"],
                    row["raw_candidate_count"],
                    row["maximum_topology_matches"],
                    capacity,
                    max(0, row["maximum_topology_matches"] - capacity),
                    "、".join(f"{name} {count} 場" for name, count in breakdown.items()),
                ]
            )
    atomic_json(output / "qualification-paths.json", path_rows)
    with (output / "daily.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "選手",
                "日期",
                "已確認對手場數",
                "候選上界（含互斥路徑）",
                "相容路徑最多",
                "相容路徑超標",
                "確定場數超標",
                "場次",
            ]
        )
        for row in report["daily"]:
            writer.writerow(
                [
                    row["name"],
                    row["date"],
                    row["known_count"],
                    row["candidate_upper_bound"],
                    row["maximum_path_count"],
                    "是" if row["path_exceeds_cap"] else "否",
                    "是" if row["known_exceeds_cap"] else "否",
                    "、".join(m["code"] or m["match_id"] for m in row["known_matches"]),
                ]
            )
    with (output / "short-rest.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["選手", "日期", "前場", "前場打完", "後場", "後場開始", "休息分鐘", "至少需要"]
        )
        for row in rests:
            writer.writerow(
                [
                    row["name"],
                    row["date"],
                    row["first"]["code"],
                    row["first"]["play_end"],
                    row["second"]["code"],
                    row["second"]["start"],
                    row["gap_minutes"],
                    row["required_minutes"],
                ]
            )
    known_gaps = [row["gap_minutes"] for row in report["gaps"] if row["basis"] == "known"]
    summary = {
        "source_revision_id": snapshot.id,
        "source_hash": source_hash,
        "source_unchanged": True,
        "policy": policy,
        "registrations": len(snapshot.registrations),
        "participants": len(snapshot.participants),
        "matches": len(snapshot.matches),
        "known_daily_violations": len(daily),
        "known_short_rest_pairs": len(rests),
        "minimum_known_rest_minutes": min(known_gaps) if known_gaps else None,
        "maximum_known_daily_matches": max(row["known_count"] for row in report["daily"]),
        "trial_status": trial["status"],
        "published_or_applied": False,
        "event_dates": snapshot.config["event"]["event_dates"],
        "day_count": days,
        "per_person_event_capacity": capacity,
        "maximum_path_matches": max(
            (row["maximum_topology_matches"] for row in path_rows), default=0
        ),
        "people_with_paths_over_event_capacity": len(excessive_paths),
        "path_strategy": report["path_strategy"],
        "constructively_verified_overload_people": len(verified_paths),
        "proved_capacity_conflict": bool(verified_paths),
        "interpretation": "已排除互斥資格支線，對所有相容拓樸路徑預留額度。未決池內名次仍保留可能性；沒有指定勝者、放寬規則、增加日期或覆寫原賽程。",
    }
    atomic_json(output / "summary.json", summary)

    def cells(values):
        return "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>"

    daily_rows = "".join(
        cells(
            [
                r["name"],
                r["date"],
                r["known_count"],
                r["candidate_upper_bound"],
                "、".join(m["code"] or m["match_id"] for m in r["known_matches"]),
            ]
        )
        for r in daily
    )
    rest_rows = "".join(
        cells([r["name"], r["date"], r["first"]["code"], r["second"]["code"], r["gap_minutes"]])
        for r in rests
    )
    bounds_rows = "".join(
        cells(
            [
                row["name"],
                row["known_match_count"],
                row["raw_candidate_count"],
                row["maximum_topology_matches"],
                capacity,
                max(0, row["maximum_topology_matches"] - capacity),
            ]
        )
        for row in path_rows
    )
    witnesses = []
    pool_map = {pool.id: pool for pool in snapshot.pools}
    stage_map = {stage.id: stage for stage in snapshot.stages}
    for row in excessive_paths:
        maximum = row["paths"][0]
        match_rows = "".join(
            cells(
                [
                    division_names[matches[mid].division_id],
                    matches[mid].display_code or mid,
                    matches[mid].round,
                    matches[mid].kind,
                ]
            )
            for mid in sorted(
                maximum["match_ids"],
                key=lambda mid: (
                    division_names[matches[mid].division_id],
                    matches[mid].round,
                    matches[mid].bracket_position,
                ),
            )
        )
        proof = row["constructive_replay"]
        proof_note = (
            f"已通過既有成績規則重播：{proof['accepted_hypothetical_results']} 場假想成績，確實進入 {proof['match_count']} 場。"
            if proof["verified"]
            else f"尚未重播證實：{proof['reason']}"
        )
        scenario_labels = []
        for key, value in maximum["decisions"].items():
            kind, identifier = key.split(":", 1)
            if kind == "pool_rank":
                pool = pool_map[identifier]
                division_name = division_names[stage_map[pool.stage_id].division_id]
                scenario_labels.append(f"{division_name}／{pool.label}：第 {value} 名")
            elif kind == "winner_side":
                scenario_labels.append(
                    f"{matches[identifier].display_code or identifier} 場：{'A' if value == 'a' else 'B'} 方勝"
                )
            else:
                scenario_labels.append(f"抽位情境：第 {value} 籤位")
        witnesses.append(
            f'<details><summary>{escape(row["name"])}：相容路徑 {row["maximum_topology_matches"]} 場，額度 {capacity} 場</summary><p>{escape(proof_note)}</p><div class="scroll"><table><thead><tr><th>組別</th><th>場號</th><th>輪次</th><th>類型</th></tr></thead><tbody>{match_rows}</tbody></table></div><p>情境條件（非實際成績）：{escape("；".join(scenario_labels))}</p></details>'
        )
    html = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>選手出賽負荷稽核</title><style>body{{margin:0;background:#f3f6f7;color:#16191b;font:16px/1.6 "Microsoft JhengHei",sans-serif}}main{{max-width:1100px;margin:24px auto;padding:32px;background:white}}h1{{font-size:26px}}h2{{font-size:20px;margin-top:32px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:650px}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #e8eaec}}th{{font-weight:500}}a{{color:#1b6c80}}@media(max-width:640px){{main{{margin:8px;padding:20px}}}}</style><main>
<h1>選手出賽負荷稽核</h1><p>同一人跨組合計：每天最多 {policy["max_matches_per_day"]} 場；前場打完至下場開打至少 {policy["minimum_rest_minutes"]} 分鐘。</p>
<p>{len(snapshot.registrations)} 筆報名／{len(snapshot.participants)} 位選手／{len(snapshot.matches)} 場。確定對手場次的最短休息 {summary["minimum_known_rest_minutes"]:g} 分鐘，單日最多 {summary["maximum_known_daily_matches"]} 場。</p>
<p>確定場數超標 {len(daily)} 筆選手／日期；確定對手場次休息不足 {len(rests)} 對。原始賽程未修改。</p>
<p>重排試算：{escape(trial["status"])}。{escape(summary["interpretation"])}</p>
<p>賽期固定 {days} 天，每人整個賽事最多 {capacity} 場。排除互斥後的最大路徑為 {summary["maximum_path_matches"]} 場；{len(excessive_paths)} 位選手有超過總額度的可能路徑。這不是每人實際一定打這麼多場，而是現有報名／賽制無法保證所有晉級情境均符合上限。</p>
<p>{len(verified_paths)} 位選手的超量路徑已在記憶體複本輸入假想成績並通過既有規則驗證，確認並非互斥場次誤加。此為模擬情境，不代表實際賽果或主辦核准。</p>
<p><a href="daily.csv">下載逐人每日場數</a> · <a href="short-rest.csv">下載休息不足明細</a> · <a href="audit.json">完整稽核 JSON</a> · <a href="solver-trial.json">重排試算</a></p>
<p><a href="paths.csv">下載逐人相容路徑場數</a> · <a href="qualification-paths.json">完整路徑與決定 JSON</a></p>
<h2>排除互斥後的出賽負荷</h2><div class="scroll"><table><thead><tr><th>選手</th><th>確定場數</th><th>原始候選累加</th><th>相容路徑最多</th><th>總額度</th><th>超出</th></tr></thead><tbody>{bounds_rows}</tbody></table></div>
<h2>超過固定賽期額度的具體路徑</h2>{"".join(witnesses)}
<h2>已確認對手場次超過每日上限</h2><div class="scroll"><table><thead><tr><th>選手</th><th>日期</th><th>確定場數</th><th>候選上界</th><th>確定對戰場號</th></tr></thead><tbody>{daily_rows}</tbody></table></div>
<h2>已確認對手場次休息不足</h2><div class="scroll"><table><thead><tr><th>選手</th><th>日期</th><th>前場</th><th>後場</th><th>休息分鐘</th></tr></thead><tbody>{rest_rows}</tbody></table></div>
<p>來源版本：{escape(snapshot.id)}</p></main></html>"""
    (output / "index.html").write_text(html, "utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("output/operator-demo-final/full-roster")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("examples/player-workload-4x60.json"))
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.policy), ensure_ascii=True))
