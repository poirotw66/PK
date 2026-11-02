"""Inspect the local operator UI; optional mutations are limited to its fictional demo."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(url: str, output: Path, mutate_demo: bool) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report: dict = {"url": url, "screens": [], "steps": [], "errors": [], "external_requests": []}

    def save():
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )

    def step(name):
        report["steps"].append(name)
        save()
        print(name, flush=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        page.on(
            "request",
            lambda request: (
                report["external_requests"].append(request.url)
                if request.url.startswith(("http:", "https:"))
                and not request.url.startswith(url.rstrip("/"))
                else None
            ),
        )
        page.goto(url)
        page.locator("#division option").nth(1).wait_for(state="attached")

        def state():
            return page.evaluate(
                "async () => (await fetch('/api/state',{headers:{'X-Squash-Token':JSON.parse(document.getElementById('boot').textContent).token}})).json()"
            )

        current = state()
        division_id = current["divisions"][0]["id"]
        page.locator("#division").select_option(division_id)
        for width, height in [(1440, 900), (768, 1024), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            for view in ["overview", "seeds", "results", "encouragement"]:
                page.locator(f'nav a[href="#{view}"]').click()
                page.wait_for_timeout(150)
                overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                assert not overflow, (view, width)
                assert page.locator("h1").inner_text()
                page.screenshot(path=str(output / f"{view}-{width}.png"), full_page=True)
                report["screens"].append(
                    {"view": view, "width": width, "horizontal_overflow": overflow}
                )
        step("Desktop, tablet and mobile screenshots passed")
        if mutate_demo:
            assert (
                "虛構" in current["name"]
                and len(current["entries"]) == 8
                and not any(m["result"] for m in current["matches"])
            )
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator("#actor").fill("Justin")
            page.locator("#reason").fill("虛構案例瀏覽器驗收")

            def confirm_button():
                page.locator("#review").wait_for(state="visible")
                page.locator("#confirm").click()
                page.get_by_text("已儲存新版本。", exact=True).wait_for(timeout=30000)

            page.locator('nav a[href="#seeds"]').click()
            page.locator("#seed-mode").select_option("provided")
            page.get_by_role("button", name="新增種子", exact=True).click()
            row = page.locator(".seed-row").first
            seeded = current["entries"][0]["id"]
            row.locator('[data-field="entry"]').select_option(seeded)
            row.locator('[data-field="positions"]').fill("1")
            page.get_by_role("button", name="預覽種子設定", exact=True).click()
            confirm_button()
            step("Seed assignment saved through UI confirmation")
            page.get_by_role("button", name="預覽主賽抽籤", exact=True).click()
            confirm_button()
            page.get_by_role("button", name="確認籤表並更新對戰", exact=True).click()
            confirm_button()
            page.locator("#schedule").click()
            confirm_button()
            step("Main draw reviewed, confirmed and scheduled")
            before_scores = state()
            first = [
                m
                for m in before_scores["matches"]
                if m["entry_a"] and m["entry_b"] and not m["result"]
            ]
            assert len(first) == 4
            page.locator('nav a[href="#results"]').click()
            for match in first:
                if page.get_by_role("button", name="返回場次", exact=True).count():
                    page.get_by_role("button", name="返回場次", exact=True).click()
                match_row = (
                    page.locator("tbody tr")
                    .filter(has=page.get_by_text(match["code"], exact=True))
                    .first
                )
                match_row.get_by_role("button", name="登錄成績", exact=True).click()
                a_wins = match["entry_a"] != seeded
                for number in (1, 2):
                    page.get_by_label(f"第 {number} 局 {match['side_a']}", exact=True).fill(
                        "11" if a_wins else "4"
                    )
                    page.get_by_label(f"第 {number} 局 {match['side_b']}", exact=True).fill(
                        "4" if a_wins else "11"
                    )
                page.get_by_role("button", name="預覽成績", exact=True).click()
                confirm_button()
            scored = state()
            qualification = scored["eligibility"][0]
            assert len(qualification["eligible_entry_ids"]) == 3
            assert (
                next(r for r in qualification["entries"] if r["entry_id"] == seeded)["reason"]
                == "SEEDED_ENTRY"
            )
            step("Four first-round scores saved; seeded loser excluded")
            page.locator('nav a[href="#encouragement"]').click()
            page.get_by_role("button", name="預覽鼓勵賽抽籤", exact=True).click()
            page.locator("#review").wait_for(state="visible")
            assert "輪空" in page.locator("#review-body").inner_text()
            page.screenshot(path=str(output / "encouragement-review.png"), full_page=True)
            confirm_button()
            assert len(state()["matches"]) == 10
            page.locator("#schedule").click()
            confirm_button()
            step("Three-player consolation draw with bye confirmed and scheduled")
            page.locator("#export").click()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                latest = state()
                if latest["jobs"] and latest["jobs"][-1]["status"] != "running":
                    assert latest["jobs"][-1]["status"] == "completed", latest["jobs"][-1]
                    report["export"] = latest["jobs"][-1]
                    break
                page.wait_for_timeout(1500)
            else:
                raise AssertionError("Document export did not finish")
            step("PDF, Excel and spectator portal exported from UI")
        assert not report["errors"], report["errors"]
        assert not report["external_requests"], report["external_requests"]
        report["status"] = "passed"
        save()
        browser.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mutate-demo", action="store_true")
    args = parser.parse_args()
    check(args.url, args.output, args.mutate_demo)
