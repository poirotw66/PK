"""Verify generated spectator portals in installed Edge and save visual evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(paths: list[Path], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    report: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for path in paths:
            errors: list[str] = []
            requests: list[str] = []
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error, target=errors: target.append(str(error)))
            page.on("request", lambda request, target=requests: target.append(request.url))
            page.goto(path.resolve().as_uri())
            name = path.parent.name
            screenshots: list[str] = []
            for width, height, size in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
                page.set_viewport_size({"width": width, "height": height})
                if (
                    page.locator("#filter-toggle").is_visible()
                    and not page.locator("#clear").is_visible()
                ):
                    page.locator("#filter-toggle").click()
                page.locator("#clear").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                    f"{name} overflows at {width}px"
                )
                screenshot = output / f"{name}-{size}.png"
                if page.locator("#filter-toggle").is_visible():
                    page.locator("#filter-toggle").click()
                page.screenshot(path=str(screenshot), full_page=False)
                screenshots.append(str(screenshot))
                page.locator("#query").fill("__no_such_player_9b1a__")
                assert page.locator("#query").input_value() == "__no_such_player_9b1a__"
                if page.locator("#filter-toggle").is_visible():
                    page.locator("#filter-toggle").click()
                page.locator("#clear").click()
                assert page.locator("#query").input_value() == ""
                for selector in ["#date", "#division", "#court"]:
                    options = page.locator(f"{selector} option").evaluate_all(
                        "nodes => nodes.map(n => n.value).filter(Boolean)"
                    )
                    if options:
                        page.locator(selector).select_option(options[-1])
                        assert page.locator(selector).input_value() == options[-1]
                        page.locator("#clear").click()
                        assert page.locator(selector).input_value() == ""
                for tab in page.locator("[data-view]").all():
                    if page.locator("#menu").is_visible():
                        page.locator("#menu").click()
                    tab.click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                        f"{name} {tab.get_attribute('data-view')} overflows at {width}px"
                    )
                if page.locator("#menu").is_visible():
                    page.locator("#menu").click()
                page.locator("[data-view]").first.click()
            assert not errors, errors
            external = [url for url in requests if url.startswith(("https:", "http:"))]
            assert not external, external
            report.append(
                {
                    "path": str(path),
                    "status": "passed",
                    "page_errors": errors,
                    "external_requests": external,
                    "screenshots": screenshots,
                }
            )
            page.close()
        browser.close()
    (output / "report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/portal-browser"))
    args = parser.parse_args()
    check(args.paths, args.output)
