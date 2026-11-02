"""Rehearse live polygon score updates on an independent full-roster copy."""

from __future__ import annotations

import argparse
import re
import threading
from pathlib import Path
from time import perf_counter

from check_reader_formats import act, score
from playwright.sync_api import expect, sync_playwright

from squash_scheduler.domain.models import content_hash
from squash_scheduler.operations import OperatorService
from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository, atomic_json


def check(output: Path, source: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh output directory")
    original = Repository(source)
    snapshot = original.load()
    original_hash = content_hash(snapshot)
    repo = Repository(output / "workspace")
    snapshot.config["event"]["name"] = "循環對戰圖 · 獨立驗收模擬"
    with repo.lock():
        repo.save(snapshot, None)
    service = OperatorService(repo)
    server = create_server(repo, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {"status": "running", "steps": [], "errors": [], "screens": []}
    atomic_json(output / "report.json", report)
    try:
        public = service.public_snapshot()
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/live/")
            expect(page.locator("#connection")).to_have_text("已同步")
            expected_edges = 0
            for group in public["standings"]:
                ids = {r["entry_id"] for r in group["rows"]}
                graph = page.locator(
                    f'.round-robin-graph[data-pool-id="{group["pool_id"] or group["stage_id"]}"]'
                )
                pairs = [
                    m
                    for m in public["matches"]
                    if m["stage_id"] == group["stage_id"]
                    and m["side_a"]["entry_id"] in ids
                    and m["side_b"]["entry_id"] in ids
                ]
                expect(graph.locator(".polygon-player")).to_have_count(len(ids))
                expect(graph.locator(".polygon-edge")).to_have_count(len(pairs))
                expected_edges += len(pairs)
            expect(page.locator(".polygon-edge")).to_have_count(expected_edges)
            expect(page.locator(".polygon-score")).to_have_count(0)
            report["steps"].append(
                f"All {len(public['standings'])} groups: actual players and {expected_edges} pairings, no invented scores"
            )

            stages = {
                stage["id"]: [g for g in public["standings"] if g["stage_id"] == stage["id"]]
                for stage in public["stages"]
                if stage["type"] == "round_robin"
            }
            multiple = {sid: groups for sid, groups in stages.items() if len(groups) > 1}
            expect(page.locator(".rr-overview")).to_have_count(len(multiple))
            for sid, groups in multiple.items():
                section = page.locator(f'[id="reader-bracket-stage-{sid}"]')
                expect(section.locator(".rr-overview .round-robin-graph")).to_have_count(
                    len(groups)
                )
                expect(section.locator(".rr-pool-detail")).to_have_count(len(groups))
                expect(section.locator(".rr-pool-detail .round-robin-graph")).to_have_count(0)
                assert section.evaluate(
                    "el=>el.querySelector('.rr-overview').getBoundingClientRect().bottom<=el.querySelector('.rr-pool-details').getBoundingClientRect().top"
                ), "Every graph must precede all per-pool details"
                for group in groups:
                    members = {r["entry_id"] for r in group["rows"]}
                    expected = {
                        m["id"]
                        for m in public["matches"]
                        if m["stage_id"] == sid
                        and m["side_a"]["entry_id"] in members
                        and m["side_b"]["entry_id"] in members
                    }
                    detail = section.locator(f'.rr-pool-detail[data-pool-id="{group["pool_id"]}"]')
                    actual = set(
                        detail.locator(".match-list .match").evaluate_all(
                            "els=>els.map(el=>el.dataset.matchId)"
                        )
                    )
                    assert actual == expected, "Per-pool results must not include another pool"
            largest_sid, largest = max(multiple.items(), key=lambda item: len(item[1]))
            page.locator("#division").select_option(largest[0]["division_id"])
            overview = page.locator(f'[id="reader-bracket-stage-{largest_sid}"] .rr-overview')
            for width in (1440, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                filename = f"all-pools-{width}.png"
                overview.screenshot(path=str(output / filename))
                report["screens"].append(filename)
            compact = overview.locator(".round-robin-graph").first
            assert compact.locator(".polygon-scroll").evaluate(
                "el=>el.scrollWidth<=el.clientWidth+1"
            ), "Phone overview must initially show the entire graph"
            compact.locator(".rr-zoom").click()
            expect(compact.locator(".rr-zoom")).to_have_attribute("aria-pressed", "true")
            assert compact.locator(".polygon-scroll").evaluate("el=>el.scrollWidth>el.clientWidth")
            compact.screenshot(path=str(output / "phone-expanded-graph.png"))
            report["screens"].append("phone-expanded-graph.png")
            compact.locator(".rr-zoom").click()
            expect(compact.locator(".rr-zoom")).to_have_attribute("aria-pressed", "false")
            link = overview.locator(".rr-detail-link").first
            target = link.get_attribute("data-target")
            link.click()
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", target
            )
            page.reload()
            expect(page.locator("#connection")).to_have_text("已同步")
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", target
            )
            page.locator(f'[id="{target}"] .rr-overview-link').click()
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", f"reader-bracket-stage-{largest_sid}"
            )
            report["steps"].append(
                f"All {len(multiple)} multi-pool stages show every graph above isolated pool details; responsive overview and detail links survive reload"
            )

            for n in (3, 4, 5):
                group = next(g for g in public["standings"] if len(g["rows"]) == n)
                ids = {r["entry_id"] for r in group["rows"]}
                matches = [
                    m
                    for m in repo.load().matches
                    if m.stage_id == group["stage_id"]
                    and getattr(m.side_a, "entry_id", None) in ids
                    and getattr(m.side_b, "entry_id", None) in ids
                ]
                batch = [score(repo.load(), m, winner_a=i % 2 == 0) for i, m in enumerate(matches)]
                page.locator("#division").select_option(group["division_id"])
                graph = page.locator(
                    f'.round-robin-graph[data-pool-id="{group["pool_id"] or group["stage_id"]}"]'
                )
                positions = graph.locator(".polygon-player text").evaluate_all(
                    "els=>els.map(e=>[e.textContent,e.getAttribute('x'),e.getAttribute('y')])"
                )
                preview = service.preview(
                    {
                        "action": "results",
                        "results": batch,
                        "revision_id": repo.load().id,
                        "actor": "Justin",
                        "reason": "獨立對戰圖模擬",
                    }
                )
                expect(graph.locator(".polygon-score")).to_have_count(0)
                started = perf_counter()
                service.confirm({"id": preview["id"], "hash": preview["hash"]})
                expect(graph.locator(".polygon-score")).to_have_count(2 * len(batch), timeout=15000)
                report[f"{n}_players_update_seconds"] = round(perf_counter() - started, 3)
                assert positions == graph.locator(".polygon-player text").evaluate_all(
                    "els=>els.map(e=>[e.textContent,e.getAttribute('x'),e.getAttribute('y')])"
                )
                for row in batch:
                    edge = graph.locator(f'.polygon-edge[data-match-id="{row["match_id"]}"]')
                    expect(edge).to_have_attribute("aria-label", re.compile("完賽"))
                    for key in ("side_a", "side_b"):
                        match = next(m for m in matches if m.id == row["match_id"])
                        entry = getattr(match, key).entry_id
                        expected = sum(
                            g[key] > g["side_b" if key == "side_a" else "side_a"]
                            for g in row["games"]
                        )
                        expect(
                            graph.locator(
                                f'.polygon-score[data-match-id="{match.id}"][data-entry-id="{entry}"] text'
                            )
                        ).to_have_text(str(expected))
                for width in (1440, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    graph.scroll_into_view_if_needed()
                    assert not page.evaluate("()=>document.documentElement.scrollWidth>innerWidth")
                    image = f"{n}-players-{width}.png"
                    graph.screenshot(path=str(output / image))
                    report["screens"].append(image)
                assert graph.locator(".polygon-score rect").evaluate_all(
                    "els=>{const r=els.map(e=>e.getBoundingClientRect());return r.every((a,i)=>r.slice(i+1).every(b=>Math.min(a.right,b.right)-Math.max(a.left,b.left)<1||Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)<1))}"
                ), "Score labels overlap"
                graph.locator(".polygon-score").first.click()
                expect(graph.locator(".polygon-detail")).to_be_visible()
                graph.locator(".polygon-close").click()
                edge = graph.locator(".polygon-edge").first
                edge.focus()
                edge.press("Enter")
                expect(graph.locator(".polygon-detail")).to_be_visible()
                expect(graph.locator(".polygon-detail .scoreline")).to_contain_text("11:4")
                # Correct through the same preview/confirm service; open detail and geometry survive.
                correction = score(repo.load(), matches[0], winner_a=False)
                act(service, action="results", results=[correction])
                a = matches[0].side_a.entry_id
                expect(
                    graph.locator(
                        f'.polygon-score[data-match-id="{matches[0].id}"][data-entry-id="{a}"] text'
                    )
                ).to_have_text("0", timeout=15000)
                expect(graph.locator(".polygon-detail")).to_be_visible()
                expect(graph.locator(".polygon-detail .scoreline")).to_contain_text("4:11")
                graph.locator(".polygon-close").click()
                expect(graph.locator(".polygon-detail")).to_be_hidden()
                report["steps"].append(
                    f"{n} players: confirmation, per-side scores, keyboard details, correction and three responsive widths passed"
                )
            page.reload()
            expect(page.locator("#connection")).to_have_text("已同步")
            first_scroll = page.locator(".polygon-scroll").first
            assert first_scroll.evaluate("el=>el.scrollLeft>0"), "Mobile graph starts centered"
            first_scroll.evaluate("el=>{el.scrollLeft=0}")
            page.locator("#search").fill("SyntheticPlayer004")
            assert page.locator(".polygon-scroll").first.evaluate("el=>el.scrollLeft===0")
            page.locator("#search").fill("")
            page.screenshot(path=str(output / "mobile-reader.png"))
            report["steps"].append(
                "Mobile initial centering and preservation of user scroll passed"
            )
            browser.close()
        assert content_hash(original.load()) == original_hash
        assert not report["errors"], report["errors"]
        report.update(status="passed", source_content_hash=original_hash)
        return report
    finally:
        atomic_json(output / "report.json", report)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source", type=Path, default=Path("output/operator-demo-final/full-roster")
    )
    args = parser.parse_args()
    print(check(args.output, args.source))
