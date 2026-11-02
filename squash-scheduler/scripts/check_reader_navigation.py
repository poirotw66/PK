"""Rehearse chapter links, history, filters and polling on an isolated full-roster reader."""

from __future__ import annotations

import argparse
import threading
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import expect, sync_playwright

from squash_scheduler.domain.models import content_hash
from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository, atomic_json


def check(output: Path, source: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh output directory")
    original_repo = Repository(source)
    original = original_repo.load()
    before = content_hash(original)
    repo = Repository(output / "workspace")
    with repo.lock():
        repo.save(original.model_copy(deep=True), None)
    server = create_server(repo, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/live/"
    report = {"status": "running", "steps": [], "screens": [], "errors": [], "writes": []}
    atomic_json(output / "report.json", report)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, timezone_id="UTC")
            page.on("pageerror", lambda e: report["errors"].append(str(e)))
            page.on(
                "request", lambda r: report["writes"].append(r.url) if r.method != "GET" else None
            )
            page.goto(url)
            expect(page.locator("#connection")).to_have_text("已同步")

            def jump(link):
                target = link.get_attribute("data-target")
                link.click()
                expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                    "data-target", target
                )
                position = page.locator(f'[id="{target}"]').evaluate(
                    "el=>el.getBoundingClientRect().top"
                )
                assert 0 <= position < 1000, position
                if position > 100:
                    assert page.evaluate(
                        "scrollY+innerHeight>=document.documentElement.scrollHeight-2"
                    )
                assert unquote(page.url.split("#", 1)[1]) == target
                return target

            def screenshot(name, width):
                page.set_viewport_size({"width": width, "height": 1000})
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                filename = f"{name}-{width}.png"
                page.screenshot(path=str(output / filename))
                report["screens"].append(filename)

            links = page.locator("#section-nav a")
            assert links.count() > 17
            assert (
                page.locator(".reader-nav-group").count() == 15
            )  # 14 scheduled + single-entry group
            first = jump(links.nth(3))
            second = jump(links.nth(8))
            page.go_back()
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", first
            )
            page.go_forward()
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", second
            )
            page.reload()
            expect(page.locator("#connection")).to_have_text("已同步")
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", second
            )
            screenshot("desktop-chapter", 1440)
            # A new public revision re-renders content without replacing a stable index or jumping.
            page.locator("#section-nav").evaluate("el=>el.scrollTop=140")
            page.evaluate("window.navReference=document.querySelector('#section-nav a')")
            nav_scroll = page.locator("#section-nav").evaluate("el=>el.scrollTop")
            y = page.evaluate("scrollY")
            revision = page.locator("#revision").inner_text()
            with repo.lock():
                current = repo.load()
                repo.save(current, current.id)
            page.get_by_role("button", name="立即更新", exact=True).evaluate("el=>el.click()")
            expect(page.locator("#revision")).not_to_have_text(revision)
            assert abs(page.evaluate("scrollY") - y) <= 2
            assert page.evaluate("window.navReference===document.querySelector('#section-nav a')")
            assert page.locator("#section-nav").evaluate("el=>el.scrollTop") == nav_scroll
            report["steps"].append("chapter links, browser back/forward, reload and stable polling")

            page.locator(".reader-back-top").click()
            page.locator('[data-view="standings"]').click()
            expect(page.locator("#nav-context")).to_contain_text("積分與排名")
            standing = jump(page.locator("#section-nav a").nth(2))
            page.reload()
            expect(page.locator('[data-view="standings"]')).to_have_attribute(
                "aria-pressed", "true"
            )
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", standing
            )
            page.locator(".reader-back-top").click()
            page.locator("#division").select_option(index=2)
            assert page.locator(".reader-nav-group").count() <= 2
            page.locator("#search").fill("不存在的選手XYZ")
            expect(page.locator("#section-nav a")).to_have_count(0)
            expect(page.locator(".reader-nav-empty")).to_be_visible()
            page.locator("#search").fill("")
            page.locator("#division").select_option("")
            report["steps"].append("standings deep links, filtered index and empty state")

            page.locator('[data-view="schedule"]').click()
            date_links = page.locator('#section-nav a[data-target^="reader-schedule-day-"]')
            while date_links.count() < 2:
                more = page.get_by_role("button", name="顯示更多", exact=False)
                if not more.count():
                    break
                more.click()
            date_target = jump(date_links.last)
            page.reload()
            expect(page.locator('[data-view="schedule"]')).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#section-nav a[aria-current=location]")).to_have_attribute(
                "data-target", date_target
            )
            report["steps"].append("schedule date links restore rows beyond the first page")
            jump(page.locator('#section-nav a[data-target^="reader-schedule-single-"]').first)
            report["steps"].append("short sections near footer keep their selected navigation link")

            for width in (768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.locator(".reader-back-top").evaluate("el=>el.click()")
                page.locator('[data-view="bracket"]').click()
                expect(page.locator("#reader-navigation")).not_to_have_attribute("open", "")
                page.locator("#reader-navigation > summary").click()
                jump(page.locator("#section-nav a").nth(4))
                expect(page.locator("#reader-navigation")).not_to_have_attribute("open", "")
                screenshot("compact-chapter", width)
                page.locator("#reader-navigation > summary").click()
                screenshot("compact-index", width)
                page.locator("#reader-navigation > summary").click()
            report["steps"].append(
                "tablet and phone index expands, jumps, closes and fits viewport"
            )
            browser.close()
        assert not report["errors"], report["errors"]
        assert not report["writes"], report["writes"]
        assert content_hash(original_repo.load()) == before
        report.update(status="passed", source_unchanged=True, source_hash=before)
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
