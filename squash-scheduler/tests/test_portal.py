"""Safety and offline publication checks for the spectator portal."""

from __future__ import annotations

import os
from html.parser import HTMLParser
from pathlib import Path

import pytest

from squash_scheduler.exporters.portal import write_portal


def portal_model() -> dict:
    return {
        "name": "中正盃壁球賽",
        "venue": "臺北市壁球中心",
        "timezone": "Asia/Taipei",
        "draft": True,
        "matches": [
            {
                "match_id": "internal-match-id",
                "code": "F01",
                "date": "2026-10-24",
                "time": "09:00",
                "court": "第一場地",
                "court_order": 1,
                "division_id": "internal-division-id",
                "division": "男子 F 組",
                "stage_id": "internal-stage-id",
                "stage": "循環賽",
                "kind": "正式賽",
                "side_a": "王小明",
                "side_b": "陳大文",
            },
            {
                "match_id": "internal-match-id-2",
                "code": "G02",
                "date": "2026-10-25",
                "time": "10:00",
                "court": "第二場地",
                "court_order": 2,
                "division_id": "internal-division-id-2",
                "division": "女子組",
                "stage_id": "internal-stage-id-2",
                "stage": "決賽",
                "kind": "正式賽",
                "side_a": "林美玲",
                "side_b": "張雅婷",
            },
        ],
        "rule_summaries": [{"title": "男子 F 組", "lines": ["三局兩勝，每局 11 分"]}],
        "pools": [],
        "stages": [],
        "participants": [
            {"id": "person-1", "display_name": "王小明", "division_names": ["男子 F 組"]},
            {"id": "person-2", "display_name": "陳大文", "division_names": ["男子 F 組"]},
        ],
    }


class PortalMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"] or "")
        if attributes.get("src"):
            self.sources.append(attributes["src"] or "")


def test_portal_embeds_data_without_external_dependencies(tmp_path: Path) -> None:
    result = write_portal(portal_model(), tmp_path)
    assert result == tmp_path / "index.html"
    html = result.read_text("utf-8")
    markup = PortalMarkup()
    markup.feed(html)
    assert "王小明" in html
    assert "F01" in html
    assert not any(url.startswith(("https:", "http:", "//")) for url in markup.sources)
    assert "booklet.pdf" not in markup.links
    assert "schedule.xlsx" not in markup.links


def test_portal_only_links_downloads_that_exist(tmp_path: Path) -> None:
    (tmp_path / "booklet.pdf").write_bytes(b"%PDF-fixture")
    (tmp_path / "schedule.xlsx").write_bytes(b"fixture")
    result = write_portal(portal_model(), tmp_path)
    markup = PortalMarkup()
    markup.feed(result.read_text("utf-8"))
    assert "booklet.pdf" in markup.links
    assert "schedule.xlsx" in markup.links


def test_portal_names_cannot_close_the_embedded_script(tmp_path: Path) -> None:
    payload = '</script><script>window.portalInjected=true</script><img src=x onerror="alert(1)">'
    model = portal_model()
    model["name"] = payload
    model["matches"][0]["side_a"] = payload
    result = write_portal(model, tmp_path)
    html = result.read_text("utf-8")
    assert payload not in html
    assert "<script>window.portalInjected" not in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_portal_handles_empty_tournament(tmp_path: Path) -> None:
    model = portal_model()
    model.update(matches=[], participants=[], rule_summaries=[])
    result = write_portal(model, tmp_path)
    assert result.is_file()
    assert "中正盃壁球賽" in result.read_text("utf-8")


def test_portal_embeds_generated_graph_without_loading_svg_as_markup(tmp_path: Path) -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80" '
        'aria-label="男子 F 組決賽"><style>text{fill:green}</style>'
        '<text x="10" y="30">王小明</text></svg>'
    )
    (tmp_path / "bracket-reference-1.svg").write_text(svg, "utf-8")
    result = write_portal(portal_model(), tmp_path)
    html = result.read_text("utf-8")
    assert "data:image/svg+xml;base64," in html
    assert "<style>text{fill:green}</style>" not in html


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PORTAL_BROWSER_TESTS") != "1",
    reason="Set SQUASH_PORTAL_BROWSER_TESTS=1 to run installed Edge verification",
)
def test_portal_browser_safety_and_responsive_width(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    model = portal_model()
    payload = '</script><script>window.portalInjected=true</script><img src=x onerror="alert(1)">'
    model["name"] = payload
    path = write_portal(model, tmp_path)
    errors: list[str] = []
    network: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: network.append(request.url))
        page.goto(path.as_uri())
        assert page.evaluate("window.portalInjected") is None
        assert payload in page.locator("body").inner_text()
        assert not errors
        for selector, value in [
            ("#query", "F01"),
            ("#date", "2026-10-24"),
            ("#division", "男子 F 組"),
            ("#court", "第一場地"),
        ]:
            if selector == "#query":
                page.locator(selector).fill(value)
            else:
                page.locator(selector).select_option(label=value)
            text = page.locator("body").inner_text()
            assert "F01" in text
            assert "G02" not in text
            page.locator("#clear").click()
            assert "G02" in page.locator("body").inner_text()
        for width, height in [(1440, 1000), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                f"Portal overflows at {width}px"
            )
        assert not any(url.startswith(("http:", "https:")) for url in network)
        browser.close()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("SQUASH_PORTAL_BROWSER_TESTS") != "1",
    reason="Set SQUASH_PORTAL_BROWSER_TESTS=1 for installed Edge verification",
)
def test_large_schedule_search_paging_and_phone_navigation(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    model = portal_model()
    first = model["matches"][0]
    model["matches"] = [{**first, "code": f"M{i:03}"} for i in range(1, 51)]
    path = write_portal(model, tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(path.as_uri())
        assert page.locator("[data-match]").count() == 6
        page.locator("#query").fill("M050")
        assert page.locator("[data-match]").count() == 1
        assert page.locator("[data-match]").get_attribute("data-match") == "M050"
        page.locator("#clear").click()
        page.locator('[data-view="schedule"]').click()
        assert page.locator("[data-match]").count() == 40
        page.get_by_role("button", name="載入更多場次", exact=False).click()
        assert page.locator("[data-match]").count() == 50
        page.locator('[data-view="overview"]').click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("scrollTo(0,0)")
        first_row = page.locator("[data-match]").first.bounding_box()
        assert first_row is not None and first_row["y"] < 844 * 0.6
        page.locator("#menu").click()
        page.locator('[data-view="players"]').click()
        assert page.locator("#heading").inner_text() == "姓名索引"
        assert not page.locator("#sidebar").is_visible()
        page.locator("#filter-toggle").click()
        page.locator("#division").select_option(label="男子 F 組")
        assert page.locator(".person").count() == 2
        browser.close()
