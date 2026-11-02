"""Rehearse workload filtering and problem-to-schedule handoff on an isolated full roster."""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from squash_scheduler.domain.models import content_hash
from squash_scheduler.operator_server import create_server
from squash_scheduler.operator_workload import build_operator_workload
from squash_scheduler.storage.repository import Repository, atomic_json


def check(output: Path, source: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh output directory")
    original_repo = Repository(source)
    original = original_repo.load()
    source_hash = content_hash(original)
    repo = Repository(output / "workspace")
    with repo.lock():
        saved = repo.save(original.model_copy(deep=True), None)
    saved_hash = content_hash(saved)
    expected = build_operator_workload(
        saved, policy={"minimum_rest_minutes": 60, "max_matches_per_day": 4}
    )
    assert expected["status"] != "invalid"
    server = create_server(repo, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    report = {
        "status": "running",
        "steps": [],
        "screens": [],
        "errors": [],
        "expected_counts": expected["counts"],
    }
    atomic_json(output / "report.json", report)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, timezone_id="UTC"
            )
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.goto(url + "/#workload")
            expect(page.locator("#title")).to_have_text("選手負荷與衝突")
            expect(page.locator(".workload-policy > summary")).to_contain_text(
                "試算：", timeout=15000
            )
            expect(page.locator("#workload-mode")).to_have_value("requested")
            assert (
                page.evaluate("()=>workloadReports.get(workloadKey()).counts") == expected["counts"]
            )
            assert page.evaluate("()=>workloadReports.get(workloadKey()).mode") == "simulation"
            assert len(expected["participants"]) == len(original.participants)
            assert content_hash(repo.load()) == saved_hash
            report["steps"].append(
                "Requested 4/60 simulation matches the backend audit without saving policy or a revision"
            )

            # Include every participant, including a single-entry player with no assigned matches.
            zero = next(
                person
                for person in expected["participants"]
                if all(day["maximum_path_count"] == 0 for day in person["daily"])
            )
            page.locator("#workload-only-problems").uncheck()
            page.locator("#search").fill(zero["name"])
            expect(
                page.locator(f'.workload-person[data-participant-id="{zero["participant_id"]}"]')
            ).to_be_visible()
            expect(page.locator(".workload-detail")).to_have_attribute(
                "data-participant-id", zero["participant_id"]
            )
            assert all(
                "已定 0／最多 0" in value
                for value in page.locator(".workload-day-counts>span").all_text_contents()
            )
            expect(page.locator(".workload-detail")).to_contain_text("沒有已確定的出賽場次")
            page.locator("#search").fill("")
            page.locator("#workload-only-problems").check()

            state_matches = {m["id"]: m for m in page.evaluate("data.matches")}
            person = next(
                person
                for person in expected["participants"]
                if person["known_gaps"]
                and any(
                    all(state_matches[mid]["schedule_editable"] for mid in c["match_ids"])
                    for c in person["conflicts"]
                )
            )
            division = person["divisions"][0]
            page.locator("#division").select_option(division["id"])
            assert all(
                division["name"] in text
                for text in page.locator(".workload-person-name").all_text_contents()
            )
            page.locator("#search").fill(person["name"])
            expect(page.locator(".workload-person")).to_have_count(1)
            expect(page.locator(".workload-detail")).to_have_attribute(
                "data-participant-id", person["participant_id"]
            )
            page.locator("#workload-only-problems").uncheck()
            chosen_day = next(day for day in person["daily"] if day["known_count"] > 1)
            page.locator("#workload-day").select_option(chosen_day["date"])
            expect(page.locator(".workload-daily")).to_have_count(1)
            expect(page.locator(".workload-daily")).to_have_attribute(
                "data-date", chosen_day["date"]
            )
            expect(page.locator(".workload-daily")).to_contain_text(
                f"已確定 {chosen_day['known_count']} 場"
            )
            expect(page.locator(".workload-daily")).to_contain_text("最短預定休息")
            page.locator("#workload-day").select_option("")
            page.locator("#workload-only-problems").check()
            report["steps"].append(
                "Global name/division filters, date filter, zero-match players and known daily rest details"
            )

            def screenshots(prefix):
                for width in (1440, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    if prefix in {"overview", "person-detail"}:
                        back = page.get_by_role("button", name="返回選手清單", exact=True)
                        if back.is_visible():
                            back.click()
                        if prefix == "person-detail":
                            page.locator(
                                f'.workload-person[data-participant-id="{person["participant_id"]}"]'
                            ).click()
                            if width == 390:
                                expect(page.locator(".workload-view")).to_have_class(
                                    "workload-view is-detail"
                                )
                                expect(page.locator(".workload-people")).not_to_be_visible()
                            page.locator(".workload-detail").scroll_into_view_if_needed()
                        else:
                            expect(page.locator(".workload-people")).to_be_visible()
                            page.evaluate("scrollTo(0,0)")
                    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"), (
                        width
                    )
                    filename = f"{prefix}-{width}.png"
                    page.screenshot(path=str(output / filename))
                    report["screens"].append(filename)

            page.locator("#division").select_option("")
            page.locator("#search").fill("")
            screenshots("overview")
            page.locator("#search").fill(person["name"])
            page.locator(
                f'.workload-person[data-participant-id="{person["participant_id"]}"]'
            ).click()
            page.locator(".workload-detail").scroll_into_view_if_needed()
            screenshots("person-detail")
            page.get_by_role("button", name="返回選手清單", exact=True).click()
            page.locator("#workload-mode").select_option("configured")
            expect(page.locator(".workload-policy > summary")).to_contain_text(
                "目前規則：", timeout=15000
            )
            assert page.evaluate("()=>workloadReports.get(workloadKey()).mode") == "configured"
            assert content_hash(repo.load()) == saved_hash
            page.locator("#workload-mode").select_option("requested")
            expect(page.locator(".workload-policy > summary")).to_contain_text("試算：")
            page.locator(".workload-filter-disclosure > summary").click()
            expect(page.locator("#workload-basis")).to_be_visible()
            page.locator("#workload-basis").select_option("known")
            page.locator("#workload-basis").select_option("all")
            page.locator(
                f'.workload-person[data-participant-id="{person["participant_id"]}"]'
            ).click()
            expect(page.locator(".workload-detail")).to_be_visible()
            report["steps"].append(
                "Phone list/detail navigation, collapsible filters and breakpoint resizing keep controls usable"
            )

            conflict = next(
                c
                for c in person["conflicts"]
                if all(
                    state_matches[mid]["schedule_editable"]
                    and not any(
                        state_matches[mid]["assignment"]["locks"].get(key)
                        for key in ("time", "start_at")
                    )
                    for mid in c["match_ids"]
                )
            )
            article = page.locator(f'.workload-conflict[data-conflict-id="{conflict["id"]}"]')
            article.get_by_role("button", name="選取相關場次調整", exact=True).click()
            expect(page.locator("#title")).to_have_text("賽程調整")
            assert set(page.evaluate("[...scheduleSelection]")) == set(conflict["match_ids"])
            dates = expected["event_dates"]
            active_day = page.evaluate("scheduleDay")
            for day in dates:
                page.locator(".schedule-tabs").get_by_role(
                    "button", name=day[5:].replace("-", "/"), exact=True
                ).click()
                assert set(page.evaluate("[...scheduleSelection]")) == set(conflict["match_ids"])
            page.locator(".schedule-tabs").get_by_role(
                "button", name=active_day[5:].replace("-", "/"), exact=True
            ).click()
            page.locator("#batch-minutes").fill("15")
            page.locator("#batch-shift").click()
            expect(page.locator("#manual-preview")).to_be_enabled()
            page.wait_for_function(
                "()=>{const raw=localStorage.getItem(ScheduleDraftStore.prefix+encodeURIComponent(data.workspace_id));return raw&&JSON.parse(raw).updates.length===scheduleDraft.size&&scheduleDraft.size>0}"
            )
            record = page.evaluate(
                "()=>JSON.parse(localStorage.getItem(ScheduleDraftStore.prefix+encodeURIComponent(data.workspace_id)))"
            )
            assert {u["match_id"] for u in record["updates"]} == set(conflict["match_ids"])
            assert record["base_revision_id"] == saved.id
            assert content_hash(repo.load()) == saved_hash
            screenshots("selected-draft")
            report["steps"].append(
                "Problem handoff selects the exact matches across dates; batch edits auto-save locally without writing the event"
            )
            # Close without a native unload prompt; no simulated draft is confirmed.
            page.on("dialog", lambda dialog: dialog.accept())
            browser.close()
        assert not report["errors"], report["errors"]
        assert content_hash(original_repo.load()) == source_hash
        assert content_hash(repo.load()) == saved_hash
        report.update(
            status="passed", source_unchanged=True, source_hash=source_hash, clone_unchanged=True
        )
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
