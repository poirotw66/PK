"""Rehearse real operator-to-reader updates and an alternate static JSON source."""

from __future__ import annotations

import argparse
import base64
import http.client
import importlib.util
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from squash_scheduler.operator_server import create_server
from squash_scheduler.public_feed import PublicFeed
from squash_scheduler.storage.repository import Repository


def check(output: Path, source: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "live_demo", project / "examples/build_live_operator_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(output / "workspace", source)
    repository = Repository(output / "workspace")
    report: dict = {
        "status": "running",
        "steps": [],
        "screens": [],
        "errors": [],
        "reader_writes": [],
    }

    def step(message: str) -> None:
        report["steps"].append(message)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
        print(message, flush=True)

    servers = []

    def run_server(server):
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}"

    url = run_server(create_server(repository, 0))
    try:
        with sync_playwright() as playwright:
            edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
            browser = playwright.chromium.launch(
                headless=True, **({"executable_path": str(edge)} if edge.exists() else {})
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, timezone_id="UTC"
            )
            operator, reader = context.new_page(), context.new_page()
            reader_requests = []
            for page in (operator, reader):
                page.on("pageerror", lambda error: report["errors"].append(str(error)))

            def observe(request):
                reader_requests.append(request)
                if request.method != "GET":
                    report["reader_writes"].append(request.url)

            reader.on("request", observe)
            operator.goto(url)
            operator.locator("#division option").nth(1).wait_for(state="attached")
            reader.goto(url + "/live/")
            expect(reader.locator("#connection")).to_have_text("已同步")
            public = reader.evaluate("async()=>await (await fetch('/public/snapshot.json')).json()")
            first = next(
                m
                for m in public["matches"]
                if m["round"] == 1 and m["side_a"]["entry_id"] and m["side_b"]["entry_id"]
            )
            target = next(
                m
                for m in public["matches"]
                if any(m[k]["source_match_id"] == first["id"] for k in ("side_a", "side_b"))
            )
            side = next(
                k for k in ("side_a", "side_b") if target[k]["source_match_id"] == first["id"]
            )
            target_name = reader.locator(
                f'[data-match-id="{target["id"]}"] [data-side="{side}"] .side-name'
            )
            expect(target_name).to_have_text(target[side]["name"])
            reader.locator("#division").select_option(first["division_id"])
            reader.locator("#search").fill(first["side_a"]["name"])
            reader.set_viewport_size({"width": 768, "height": 1000})
            reader.locator(".bracket-scroll").first.evaluate("el=>el.scrollLeft=80")
            reader.evaluate("scrollTo(0,250)")
            horizontal = reader.locator(".bracket-scroll").first.evaluate("el=>el.scrollLeft")
            vertical = reader.evaluate("scrollY")
            operator.locator("#actor").fill("Justin")
            operator.locator("#reason").fill("虛構即時閱讀頁雙視窗驗收")
            operator.locator('nav a[href="#results"]').click()
            row = (
                operator.locator("tbody tr")
                .filter(has=operator.get_by_text(first["code"], exact=True))
                .first
            )
            row.get_by_role("button", name="登錄成績", exact=True).click()

            def scores(a_wins: bool):
                for number in (1, 2):
                    operator.get_by_label(
                        f"第 {number} 局 {first['side_a']['name']}", exact=True
                    ).fill("11" if a_wins else "4")
                    operator.get_by_label(
                        f"第 {number} 局 {first['side_b']['name']}", exact=True
                    ).fill("4" if a_wins else "11")

            def confirm():
                operator.locator("#review").wait_for(state="visible")
                operator.locator("#confirm").click()
                expect(operator.get_by_text("已儲存新版本。", exact=True)).to_be_visible()

            scores(True)
            operator.get_by_role("button", name="預覽成績", exact=True).click()
            operator.locator("#review").wait_for(state="visible")
            reader.wait_for_timeout(3400)
            expect(target_name).to_have_text(target[side]["name"])
            step("Unconfirmed operator preview does not change public bracket")
            confirm()
            expect(target_name).to_have_text(first["side_a"]["name"], timeout=12000)
            assert reader.locator("#search").input_value() == first["side_a"]["name"]
            assert reader.locator("#division").input_value() == first["division_id"]
            assert (
                abs(
                    reader.locator(".bracket-scroll").first.evaluate("el=>el.scrollLeft")
                    - horizontal
                )
                < 2
            )
            assert abs(reader.evaluate("scrollY") - vertical) < 2
            step("Confirmed score advances winner live while preserving search, filters and scroll")
            scores(False)
            operator.get_by_role("button", name="預覽成績更正", exact=True).click()
            confirm()
            expect(target_name).to_have_text(first["side_b"]["name"], timeout=12000)
            step("Legal result correction updates next-round opponent without reloading reader")
            reader.locator("#search").fill("")
            for width in (1440, 768, 390):
                reader.set_viewport_size({"width": width, "height": 1000})
                for view in ("bracket", "schedule"):
                    reader.locator(f'[data-view="{view}"]').click()
                    reader.wait_for_timeout(150)
                    assert not reader.evaluate("document.documentElement.scrollWidth>innerWidth"), (
                        width,
                        view,
                    )
                    reader.screenshot(path=str(output / f"{view}-{width}.png"), full_page=True)
                    report["screens"].append(
                        {"view": view, "width": width, "horizontal_overflow": False}
                    )
            context.set_offline(True)
            expect(reader.locator("#connection")).to_contain_text("資料可能過期", timeout=15000)
            assert reader.locator(".schedule-row").count()
            reader.screenshot(path=str(output / "offline.png"), full_page=True)
            context.set_offline(False)
            expect(reader.locator("#connection")).to_have_text("已同步", timeout=15000)
            step(
                "Disconnect retains last content with stale notice; reconnect recovers automatically"
            )

            # An independent restore can retain a revision ID, but not its public scope.
            secret = operator.evaluate(
                "JSON.parse(document.getElementById('boot').textContent).token"
            )
            metadata = {
                "revision_id": repository.load().id,
                "actor": "Justin",
                "reason": "虛構工作區切換驗收",
            }

            def post(path, body, *, binary=False):
                headers = {
                    "X-Squash-Token": secret,
                    "Content-Type": "application/zip" if binary else "application/json",
                }
                if binary:
                    headers["X-Restore-Metadata"] = base64.b64encode(
                        json.dumps(metadata).encode()
                    ).decode()
                connection = http.client.HTTPConnection("127.0.0.1", urlsplit(url).port, timeout=30)
                connection.request(
                    "POST",
                    path,
                    body=body if binary else json.dumps(body).encode(),
                    headers=headers,
                )
                response = connection.getresponse()
                value = response.read()
                connection.close()
                assert response.status == 200, value.decode("utf-8")
                return json.loads(value)

            old_workspace = public["workspace_id"]
            backup = post("/api/backup", metadata)
            connection = http.client.HTTPConnection("127.0.0.1", urlsplit(url).port, timeout=30)
            connection.request("GET", backup["url"])
            response = connection.getresponse()
            archive = response.read()
            connection.close()
            assert response.status == 200
            plan = post("/api/restore-preview", archive, binary=True)
            restored = post("/api/restore-confirm", {"id": plan["id"], "hash": plan["hash"]})
            post("/api/switch-workspace", {**metadata, "restore_id": restored["restore_id"]})
            expect(reader.locator("#workspace")).not_to_contain_text(
                old_workspace[:8], timeout=12000
            )
            reader.screenshot(path=str(output / "workspace-switch.png"), full_page=True)
            post("/api/switch-workspace", {**metadata, "restore_id": "original"})
            expect(reader.locator("#workspace")).to_contain_text(old_workspace[:8], timeout=12000)
            step(
                "Restored child with the same revision changes public scope; switching back also updates reader"
            )

            # A separate static server models an object-storage origin, with no operator API.
            static_root = output / "public-fixture"
            (static_root / "snapshots").mkdir(parents=True)
            feed = PublicFeed()
            frozen = feed.read(repository)
            head = feed.version(repository)
            head["snapshot_url"] = "snapshots/current.json"
            (static_root / "version.json").write_text(json.dumps(head, ensure_ascii=False), "utf-8")
            (static_root / "snapshots/current.json").write_text(
                json.dumps(frozen, ensure_ascii=False), "utf-8"
            )

            class Static(SimpleHTTPRequestHandler):
                def end_headers(self):
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Headers", "If-None-Match")
                    super().end_headers()

                def do_OPTIONS(self):
                    self.send_response(204)
                    self.end_headers()

                def log_message(self, *args):
                    pass

            static_url = run_server(
                ThreadingHTTPServer(("127.0.0.1", 0), partial(Static, directory=str(static_root)))
            )
            alternate_url = run_server(
                create_server(
                    repository,
                    0,
                    reader_config={
                        "source": {
                            "type": "http-json",
                            "version_url": static_url + "/version.json",
                        },
                        "poll_interval_ms": 3000,
                    },
                )
            )
            alternate = context.new_page()
            alternate.on("pageerror", lambda error: report["errors"].append(str(error)))
            calls = []
            alternate.on("request", lambda request: calls.append(request.url))
            alternate.goto(alternate_url + "/live/")
            expect(alternate.locator("#connection")).to_have_text("已同步")
            assert any(url.startswith(static_url + "/snapshots/") for url in calls)
            assert not any(
                url.startswith(alternate_url + "/public/") or "/api/state" in url for url in calls
            )
            alternate.screenshot(path=str(output / "alternate-source.png"), full_page=True)
            step("Same reader and bracket render from alternate static JSON origin by config alone")
            assert not report["reader_writes"]
            assert not any(
                "X-Squash-Token".lower() in request.headers for request in reader_requests
            )
            assert not report["errors"], report["errors"]
            report["status"] = "passed"
            step(
                "Read-only reader sends no write requests or operator credentials; zero JavaScript errors"
            )
            browser.close()
    finally:
        for server in reversed(servers):
            server.shutdown()
            server.server_close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("output/operator-demo-final/junior"))
    args = parser.parse_args()
    check(args.output, args.source)
