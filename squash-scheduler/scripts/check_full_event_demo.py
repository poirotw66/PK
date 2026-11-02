"""Verify full original-source and four-court demos using the delivered HTML files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(project: Path, simulation: Path | None = None, output: Path | None = None) -> None:
    output = output or project / "output/full-event-browser-qa"
    output.mkdir(parents=True, exist_ok=True)
    cases = (
        [("simulation", simulation.resolve(), 279, None)]
        if simulation
        else [
            ("published", project / "output/full-event-demo/index.html", 232, 25),
            ("simulation", project / "output/full-demo-four-courts/index.html", 279, None),
        ]
    )
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for name, path, match_count, graph_count in cases:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            external: list[str] = []
            page.on("pageerror", lambda error, target=errors: target.append(str(error)))
            page.on(
                "request",
                lambda request, target=external: (
                    target.append(request.url)
                    if request.url.startswith(("http:", "https:"))
                    else None
                ),
            )
            page.goto(path.as_uri())
            data = json.loads(page.locator("#data").text_content())
            assert len(data["matches"]) == match_count
            assert len(data["roster"]) == 147
            assert len({r["name"] for r in data["roster"]}) == 114
            assert len({r["division"] for r in data["roster"]}) == 17
            assert set(data["court_labels"]) == {"場地A", "場地B", "場地C", "場地D"}
            page.locator('[data-view="players"]').click()
            assert page.locator(".person").count() == 114
            assert "147" in page.locator("#count").inner_text()
            page.locator("#division").select_option(label="男子A組")
            assert page.locator(".person").count() == 1
            page.locator('[data-view="brackets"]').click()
            assert page.locator(".graph-card").count() == 0
            page.locator("#clear").click()
            assert page.locator(".graph-card").count() > 2
            if graph_count:
                assert page.locator(".graph-card").count() == graph_count
            page.wait_for_function(
                "[...document.querySelectorAll('.graph-card img')].every(i=>i.complete&&i.naturalWidth>0)"
            )
            page.locator("#division").select_option(label="女子D組")
            assert page.locator(".graph-card").count() > 0
            page.locator(".graph-head button").first.click()
            assert page.locator("#zoom").is_visible()
            page.locator("#close").click()
            page.locator("#clear").click()
            page.locator('[data-view="schedule"]').click()
            if name == "simulation":
                page.locator("#court").select_option(label="場地D")
                assert page.locator("[data-match]").count() > 0
                assert all(
                    value == "場地D" for value in page.locator(".match-court").all_text_contents()
                )
                page.locator("#clear").click()
            page.locator('[data-view="overview"]').click()
            for width, height, suffix in [(1440, 1000, "desktop"), (390, 844, "mobile")]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.evaluate("scrollTo(0,0)")
                row = page.locator("[data-match]").first.bounding_box()
                assert row is not None and row["y"] < height * 0.6
                page.screenshot(path=str(output / f"{name}-{suffix}.png"))
            assert not errors and not external, (errors, external)
            reports.append(
                {
                    "case": name,
                    "passed": True,
                    "registrations": 147,
                    "participants": 114,
                    "divisions": 17,
                    "matches": match_count,
                    "graphs": len(data["graphs"]),
                    "courts": data["court_labels"],
                    "javascript_errors": errors,
                    "external_requests": external,
                }
            )
            page.close()
        browser.close()
    (output / "report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(reports, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulation", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    check(Path(__file__).resolve().parents[1], args.simulation, args.output)
