"""Real-browser standings, special-format linkage and full-roster rehearsal evidence."""

from __future__ import annotations

import argparse
import importlib.util
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright

from squash_scheduler.domain.deferred_draws import bind_deferred_execution, realize_deferred_draw
from squash_scheduler.domain.models import canonical_json, content_hash, new_id
from squash_scheduler.domain.rankings import ranking_basis, resolve_side
from squash_scheduler.domain.revisions import approve
from squash_scheduler.domain.validation import target_basis, validate
from squash_scheduler.operations import OperatorService
from squash_scheduler.operator_server import create_server
from squash_scheduler.public_feed import PublicFeed
from squash_scheduler.scheduling import solve_schedule
from squash_scheduler.storage.repository import Repository, atomic_json


def score(snapshot, match, winner_a=True):
    needed = match.rule_snapshot["match_rules"]["best_of_games"] // 2 + 1
    return {
        "match_id": match.id,
        "status": "confirmed",
        "outcome": "played",
        "winner_id": resolve_side(snapshot, match.side_a if winner_a else match.side_b),
        "games": [
            {"side_a": 11, "side_b": 4} if winner_a else {"side_a": 4, "side_b": 11}
            for _ in range(needed)
        ],
    }


def save(repo, snapshot):
    with repo.lock():
        return repo.save(snapshot, snapshot.id)


def act(service, **operation):
    proposal = service.preview(
        {
            "revision_id": service.repo.load().id,
            "actor": "Justin",
            "reason": "獨立虛構驗收",
            **operation,
        }
    )
    service.confirm({"id": proposal["id"], "hash": proposal["hash"]})
    return proposal


def check(output: Path, source: Path, full_source: Path) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Choose a fresh acceptance output")
    spec = importlib.util.spec_from_file_location(
        "reader_scenarios",
        Path(__file__).resolve().parents[1] / "examples/build_reader_scenarios.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(output / "cases", source)
    report = {"status": "running", "steps": [], "screens": [], "errors": [], "performance": {}}
    servers, services = [], []

    def step(message):
        report["steps"].append(message)
        atomic_json(output / "report.json", report)
        print(message, flush=True)

    def start(path):
        repo = Repository(path)
        service = OperatorService(repo)
        services.append(service)
        server = create_server(repo, 0)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return repo, service, f"http://127.0.0.1:{server.server_port}"

    try:
        with sync_playwright() as playwright:
            edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
            browser = playwright.chromium.launch(
                headless=True, **({"executable_path": str(edge)} if edge.exists() else {})
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, timezone_id="UTC"
            )
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))

            def view(url):
                page.goto(url + "/live/")
                expect(page.locator("#connection")).to_have_text("已同步", timeout=15000)

            def screens(prefix, views=("bracket", "standings", "schedule")):
                for width in (1440, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    for name in views:
                        page.locator(f'[data-view="{name}"]').click()
                        page.wait_for_timeout(100)
                        assert not page.evaluate(
                            "document.documentElement.scrollWidth>innerWidth"
                        ), (prefix, width, name)
                        page.screenshot(
                            path=str(output / f"{prefix}-{name}-{width}.png"), full_page=True
                        )
                        report["screens"].append({"case": prefix, "view": name, "width": width})

            # 1. Partial standings, a three-way tie, explicit approval and stale approval.
            repo, service, url = start(output / "cases/rr")
            view(url)
            page.locator('[data-view="standings"]').click()
            expect(page.locator(".standings-table tbody tr")).to_have_count(3)
            assert "勝:2分 敗:1分 棄:0分" in page.locator("#content").inner_text()
            snapshot = repo.load()
            entries = sorted(e.id for e in snapshot.entries)
            cycle = {entries[0]: entries[1], entries[1]: entries[2], entries[2]: entries[0]}
            batch = [
                score(snapshot, m, cycle[m.side_a.entry_id] == m.side_b.entry_id)
                for m in snapshot.matches
            ]
            plan = service.preview(
                {
                    "revision_id": snapshot.id,
                    "actor": "Justin",
                    "reason": "Synthetic first result",
                    "action": "results",
                    "results": batch[:1],
                }
            )
            assert (
                sum(row["points"] for row in service.public_snapshot()["standings"][0]["rows"]) == 0
            )
            service.confirm({"id": plan["id"], "hash": plan["hash"]})
            page.wait_for_function(
                "()=>[...document.querySelectorAll('.standings-table tbody tr')].reduce((s,r)=>s+Number(r.cells[2].textContent),0)===3"
            )
            assert "名次待全組結果確認" in page.locator("#content").inner_text()
            act(service, action="results", results=batch[1:])
            expect(page.locator(".ranking-pending")).to_contain_text("同分待裁定", timeout=12000)
            assert all(
                text == "待核定"
                for text in page.locator(
                    ".standings-table tbody tr td:nth-child(2)"
                ).all_text_contents()
            )
            page.screenshot(path=str(output / "rr-tie.png"), full_page=True)
            snapshot = repo.load()
            stage = snapshot.stages[0]
            decision = {
                "id": new_id(),
                "stage_id": stage.id,
                "pool_id": None,
                "ordered_entry_ids": list(reversed(entries)),
                "basis_hash": ranking_basis(snapshot, stage.id),
                "reason": "PRIVATE-SYNTHETIC-ARBITRATION",
                "status": "draft",
                "approval_id": None,
            }
            snapshot.ranking_decisions.append(decision)
            snapshot = save(repo, snapshot)
            assert service.public_snapshot()["standings"][0]["status"] == "manual_review"
            snapshot, _ = approve(
                snapshot,
                f"ranking:{decision['id']}",
                target_basis(decision),
                "reviewer",
                decision["reason"],
            )
            save(repo, snapshot)
            expect(page.locator(".standings-group")).to_contain_text("排名已確認", timeout=12000)
            assert page.locator(
                ".standings-table tbody tr td:nth-child(2)"
            ).all_text_contents() == ["1", "2", "3"]
            assert "PRIVATE-SYNTHETIC-ARBITRATION" not in page.content()
            screens("rr")
            correction = {
                **batch[0],
                "games": [
                    {"side_a": 11, "side_b": 5}
                    if batch[0]["games"][0]["side_a"] == 11
                    else {"side_a": 5, "side_b": 11}
                    for _ in batch[0]["games"]
                ],
            }
            act(service, action="results", results=[correction])
            page.locator('[data-view="standings"]').click()
            expect(page.locator(".ranking-pending")).to_contain_text("同分待裁定", timeout=12000)
            step(
                "1: standings update after confirmation; ties wait for approval and result correction invalidates old approval"
            )

            # 2a. F pools must finish, then an executed draw must be approved and activated.
            repo, service, url = start(output / "cases/f")
            view(url)
            snapshot = repo.load()
            rr_ids = {s.id for s in snapshot.stages if s.type == "round_robin"}
            act(
                service,
                action="results",
                results=[score(snapshot, m) for m in snapshot.matches if m.stage_id in rr_ids],
            )
            page.wait_for_function("()=>document.querySelectorAll('.qualifier-label').length===6")
            before = service.public_snapshot()
            assert all(
                side["entry_id"] is None
                for m in before["matches"]
                for side in (m["side_a"], m["side_b"])
                if side["source_type"] == "draw_slot"
            )
            snapshot = repo.load()
            firsts = [
                next(row["entry_id"] for row in group["rows"] if row["rank"] == 1)
                for group in before["standings"]
            ]
            seconds = [
                next(row["entry_id"] for row in group["rows"] if row["rank"] == 2)
                for group in before["standings"]
            ]
            declaration = next(d for d in snapshot.draws if d.get("kind") == "deferred_slots")
            end = max(
                a.play_end_at
                for a in snapshot.assignments
                if any(m.id == a.match_id and m.stage_id in rr_ids for m in snapshot.matches)
            )
            execution = realize_deferred_draw(
                snapshot,
                {
                    "draw_run_id": declaration["id"],
                    "assignments": [
                        {
                            "slot_id": str(i),
                            "entry_id": (firsts if i in {1, 3, 6} else seconds).pop(0),
                        }
                        for i in range(1, 7)
                    ],
                    "executed_at": (end + timedelta(minutes=15)).isoformat(),
                    "evidence": "PRIVATE-F-DRAW-SHEET",
                },
                "tester",
                "Fictional onsite execution",
            )
            snapshot = save(repo, snapshot)
            pending = service.public_snapshot()
            assert not any(
                side["entry_id"]
                for m in pending["matches"]
                for side in (m["side_a"], m["side_b"])
                if side["source_type"] == "draw_slot"
            )
            snapshot, _ = approve(
                snapshot,
                f"deferred_draw:{execution['id']}",
                target_basis(execution),
                "reviewer",
                "Confirm fictional draw",
            )
            bind_deferred_execution(snapshot, execution["id"])
            assert solve_schedule(snapshot, time_limit_seconds=10)["status"] in {
                "FEASIBLE",
                "OPTIMAL",
            }
            assert validate(snapshot, "G4")["passed"]
            save(repo, snapshot)
            after = service.public_snapshot()
            for match in after["matches"]:
                for key in ("side_a", "side_b"):
                    side = match[key]
                    if side["source_type"] == "draw_slot":
                        expect(
                            page.locator(
                                f'[data-match-id="{match["id"]}"] [data-side="{key}"] .side-name'
                            )
                        ).to_have_text(side["name"], timeout=12000)
            assert "PRIVATE-F-DRAW-SHEET" not in page.content()
            screens("f")
            step(
                "2: F firsts draw positions 1/3/6 in a fictional approved procedure; six names appear only after approval and activation"
            )

            # 2b. Seeded first-round loser is excluded; three qualified entrants include one bye.
            repo, service, url = start(output / "cases/junior")
            snapshot = repo.load()
            division = snapshot.divisions[0]
            seeded = snapshot.entries[0].id
            act(
                service,
                action="seeds",
                division_id=division.id,
                mode="provided",
                seeds=[{"entry_id": seeded, "seed_number": 1, "allowed_positions": ["1"]}],
            )
            act(service, action="draw", division_id=division.id, seed=717)
            act(service, action="approve_draw", draw_id=repo.load().draws[-1]["id"])
            act(service, action="schedule")
            view(url)
            snapshot = repo.load()
            first = [m for m in snapshot.matches if m.kind == "competitive" and m.round == 1]
            act(
                service,
                action="results",
                results=[
                    score(snapshot, m, resolve_side(snapshot, m.side_a) != seeded) for m in first
                ],
            )
            plan = service.preview(
                {
                    "revision_id": repo.load().id,
                    "actor": "Justin",
                    "reason": "Qualified fictional encouragement",
                    "action": "encouragement",
                    "division_id": division.id,
                    "seed": 89,
                }
            )
            assert not any("鼓勵" in s["name"] for s in service.public_snapshot()["stages"])
            service.confirm({"id": plan["id"], "hash": plan["hash"]})
            public = service.public_snapshot()
            encouragement = next(s for s in public["stages"] if s["name"] == "鼓勵賽")
            assert len(encouragement["bye_names"]) == 1
            qualified = {
                side["entry_id"]
                for m in public["matches"]
                if m["stage_id"] == encouragement["id"]
                for side in (m["side_a"], m["side_b"])
                if side["entry_id"]
            }
            assert len(qualified) == 3 and seeded not in qualified
            expect(page.locator("#content")).to_contain_text("首輪輪空", timeout=12000)
            snapshot = repo.load()
            semis = [m for m in snapshot.matches if m.kind == "competitive" and m.round == 2]
            act(service, action="results", results=[score(snapshot, m) for m in semis])
            public = service.public_snapshot()
            bronze = next(
                m
                for m in public["matches"]
                if m["kind"] == "placement" and m["stage_id"] != encouragement["id"]
            )
            for key in ("side_a", "side_b"):
                expect(
                    page.locator(f'[data-match-id="{bronze["id"]}"] [data-side="{key}"] .side-name')
                ).to_have_text(bronze[key]["name"], timeout=12000)
            screens("junior", ("bracket", "schedule"))
            step(
                "2: three-player encouragement has an explicit bye and excludes seeded loser; bronze resolves semifinal losers"
            )

            # 3. Keep real source intact, rehearse in an independent full-roster repository.
            original = Repository(full_source)
            full = original.load()
            source_hash = content_hash(full)
            assert (
                len(full.registrations),
                len(full.participants),
                len(full.divisions),
                len(full.matches),
            ) == (147, 114, 17, 279)
            destination = Repository(output / "full")
            full.config["event"]["name"] = "原始名單 · 即時閱讀驗收模擬"
            with destination.lock():
                destination.save(full, None)
            repo, service, url = start(destination.root)
            feed = PublicFeed()
            started = perf_counter()
            public = feed.read(repo)
            cold = perf_counter() - started
            timings = []
            for _ in range(200):
                started = perf_counter()
                feed.version(repo)
                timings.append((perf_counter() - started) * 1000)
            assert len(public["matches"]) == 279 and len(public["divisions"]) == 17
            assert sum(d["entry_count"] for d in public["divisions"]) == 147
            assert (
                len(public["standings"]) == 38
                and sum(len(g["rows"]) for g in public["standings"]) == 144
            )
            started = perf_counter()
            view(url)
            load = perf_counter() - started
            assert page.locator("#division option").count() == 18
            for division in public["divisions"]:
                page.locator("#division").select_option(division["id"])
                count = sum(m["division_id"] == division["id"] for m in public["matches"])
                expect(page.locator("#summary")).to_have_text(f"{count} 場比賽 · 0 場完成")
                assert page.locator(".match").count() == count
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"), (
                    division["name"]
                )
                expected_rows = sum(
                    len(g["rows"])
                    for g in public["standings"]
                    if g["division_id"] == division["id"]
                )
                assert page.locator(".standings-table tbody tr").count() == expected_rows
                if division["single_entry_name"]:
                    assert division["single_entry_name"] in page.locator("#content").inner_text()
                    assert "未編排對戰" in page.locator("#content").inner_text()
            chosen = next(d for d in public["divisions"] if "F" in d["name"])
            page.locator("#division").select_option(chosen["id"])
            screens("full-f")
            page.locator("#division").select_option("")
            page.locator('[data-view="standings"]').click()
            expect(page.locator(".standings-group")).to_have_count(38)
            page.locator('[data-view="schedule"]').click()
            expect(page.locator(".schedule-row")).to_have_count(40)
            page.get_by_role("button", name="顯示更多", exact=False).click()
            expect(page.locator(".schedule-row")).to_have_count(80)
            sample = next(
                m for m in public["matches"] if m["side_a"]["entry_id"] and m["side_b"]["entry_id"]
            )
            page.locator(".more-filters summary").click()
            date = (
                datetime.fromisoformat(sample["planned_start_at"])
                .astimezone(ZoneInfo(public["timezone"]))
                .date()
                .isoformat()
            )
            page.locator("#date").select_option(date)
            page.locator("#court").select_option(sample["court_id"])
            filtered = [
                m
                for m in public["matches"]
                if m["court_id"] == sample["court_id"]
                and m["planned_start_at"]
                and datetime.fromisoformat(m["planned_start_at"])
                .astimezone(ZoneInfo(public["timezone"]))
                .date()
                .isoformat()
                == date
            ]
            expect(page.locator(".schedule-row")).to_have_count(min(40, len(filtered)))
            page.locator("#date").select_option("")
            page.locator("#court").select_option("")
            page.locator(".more-filters summary").click()
            page.locator("#search").fill(sample["side_a"]["name"])
            assert page.locator(".schedule-row").count() > 0
            page.locator("#search").fill("肯定沒有這個名字-QA")
            assert "沒有符合條件" in page.locator("#content").inner_text()
            page.locator("#search").fill(sample["code"])
            snapshot = repo.load()
            selected = next(m for m in snapshot.matches if m.id == sample["id"])
            started = perf_counter()
            act(service, action="results", results=[score(snapshot, selected)])
            page.wait_for_function(
                "()=>[...document.querySelectorAll('.schedule-row')].some(r=>r.textContent.includes('勝出'))"
            )
            sync = perf_counter() - started
            report["performance"] = {
                "cold_public_seconds": round(cold, 4),
                "warm_version_median_ms": round(statistics.median(timings), 4),
                "warm_version_p95_ms": round(sorted(timings)[189], 4),
                "reader_load_seconds": round(load, 4),
                "confirmed_to_visible_seconds": round(sync, 4),
                "snapshot_bytes": len(canonical_json(public).encode()),
                "matches": 279,
                "registrations": 147,
                "participants": 114,
                "divisions": 17,
                "standings_groups": 38,
                "standings_rows": 144,
            }
            assert cold < 10 and load < 15 and statistics.median(timings) < 50 and sync < 15
            assert content_hash(original.load()) == source_hash
            report["full_source"] = {
                "revision_id": original.load().id,
                "content_hash": source_hash,
                "unchanged": True,
            }
            step(
                "3: 147 registrations / 114 people / 17 divisions / 279 matches verified; three single-entry groups retained without fake opponents"
            )
            step(
                "3: full-roster search, 40-to-80 paging, 38 standings groups, score update and three screen widths passed; original source unchanged"
            )
            assert not report["errors"], report["errors"]
            report["status"] = "passed"
            step("All ordered acceptance phases passed with zero browser JavaScript errors")
            browser.close()
    except Exception as exc:
        report.update(status="failed", failure=str(exc))
        atomic_json(output / "report.json", report)
        raise
    finally:
        for service in services:
            service.close()
        for server in reversed(servers):
            server.shutdown()
            server.server_close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("output/operator-demo-final/junior"))
    parser.add_argument(
        "--full-source", type=Path, default=Path("output/operator-demo-final/full-roster")
    )
    args = parser.parse_args()
    check(args.output, args.source, args.full_source)
