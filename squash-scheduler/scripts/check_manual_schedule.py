"""Actual operator/browser rehearsal of manual schedule comparison and announcements."""

from __future__ import annotations

import argparse
import itertools
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright

from squash_scheduler.domain.models import content_hash
from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository, atomic_json


def check(output: Path, source: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh output directory")
    original_repo = Repository(source)
    original = original_repo.load()
    source_hash = content_hash(original)
    candidates = [
        m.id
        for m in original.matches
        if m.round == 1 and m.side_a.type == "entry" and m.side_b.type == "entry"
    ]
    a, b = next(
        (a, b)
        for a, b in itertools.combinations(original.assignments, 2)
        if a.match_id in candidates
        and b.match_id in candidates
        and a.start_at == b.start_at
        and a.court_id != b.court_id
    )
    snapshot = original.model_copy(deep=True)
    snapshot.config["event"]["name"] = "人工排程 · 獨立虛構演練"
    snapshot.assignments = [
        b.model_copy(update={"match_id": a.match_id}) if x.match_id == a.match_id else x
        for x in snapshot.assignments
    ]
    repo = Repository(output / "workspace")
    with repo.lock():
        repo.save(snapshot, None)
    server = create_server(repo, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    zone = ZoneInfo(original.config["event"]["timezone"])
    report = {"status": "running", "steps": [], "screens": [], "errors": []}
    atomic_json(output / "report.json", report)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, timezone_id="UTC", accept_downloads=True
            )
            page, reader = context.new_page(), context.new_page()
            for tab in (page, reader):
                tab.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.goto(url + "/#schedule")
            expect(page.locator("#title")).to_have_text("賽程調整")
            page.locator("#actor").fill("PRIVATE-TEST-OPERATOR")
            reader.goto(url + "/live/")
            expect(reader.locator("#connection")).to_have_text("已同步")
            old_revision = repo.load().id

            def stage(mid, assignment):
                page.locator(f'.schedule-edit[data-match-id="{mid}"]').click()
                local = assignment.start_at.astimezone(zone)
                page.locator("#manual-date").select_option(local.date().isoformat())
                page.locator("#manual-time").fill(local.strftime("%H:%M"))
                page.locator("#manual-court").select_option(assignment.court_id)
                page.locator("#manual-time").press("Tab")
                page.get_by_role("button", name="完成", exact=True).click()

            def preview():
                page.locator("#schedule-draft-details").click()
                page.locator("#manual-reason").fill("PRIVATE-TEST-REASON")
                page.get_by_role("button", name="完成", exact=True).click()
                page.locator("#manual-preview").click()
                expect(page.locator("#review")).to_be_visible()

            def screenshots(prefix):
                for width in (1440, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert not page.evaluate(
                        "()=>document.documentElement.scrollWidth>innerWidth"
                    ), (prefix, width)
                    if page.locator("#review").is_visible():
                        assert page.locator("#review").evaluate(
                            "el=>el.scrollWidth<=el.clientWidth+1"
                        )
                    filename = f"{prefix}-{width}.png"
                    page.screenshot(path=str(output / filename))
                    report["screens"].append(filename)

            for mid in (a.match_id, b.match_id):
                page.locator(f'.schedule-check[data-match-id="{mid}"]').check()
            expect(page.locator("#batch-swap")).to_be_enabled()
            page.locator("#batch-minutes").fill("15")
            page.locator("#batch-shift").click()
            expect(page.locator(".schedule-slot.is-changed")).to_have_count(2)
            assert repo.load().id == old_revision
            page.locator("#schedule-undo").click()
            expect(page.locator(".schedule-slot.is-changed")).to_have_count(0)
            page.get_by_role("button", name="改日期／場地", exact=True).click()
            page.locator("#batch-court").select_option(a.court_id)
            page.locator("#batch-apply").click()
            expect(page.locator(".schedule-slot.is-changed")).to_have_count(2)
            page.locator("#schedule-undo").click()
            # Selected IDs survive court/date filters; filtering never changes the batch.
            page.get_by_role("combobox", name="篩選場地", exact=True).select_option(a.court_id)
            expect(page.locator("#batch-swap")).to_be_enabled()
            page.get_by_role("button", name="未排程", exact=True).click()
            expect(page.locator("#batch-swap")).to_be_enabled()
            expect(page.locator(".schedule-slot")).to_have_count(0)
            day_label = a.start_at.astimezone(zone).strftime("%m/%d")
            page.get_by_role("button", name=day_label, exact=True).click()
            page.get_by_role("combobox", name="篩選場地", exact=True).select_option("")
            # Phone resizing keeps the same selection and uses a usable list.
            page.set_viewport_size({"width": 390, "height": 1000})
            page.locator("#batch-minutes").fill("-15")
            page.locator("#batch-shift").click()
            expect(page.locator(".schedule-canvas")).to_have_class("schedule-canvas schedule-list")
            expect(page.locator(".schedule-slot.is-changed")).to_have_count(2)
            page.locator("#schedule-undo").click()
            # An offset outside the fixed event dates is rejected atomically.
            page.locator("#batch-minutes").fill("-1440")
            page.locator("#batch-shift").click()
            expect(page.locator("#message")).to_contain_text("不能增加比賽天數")
            expect(page.locator(".schedule-slot.is-changed")).to_have_count(0)
            screenshots("batch-selection")
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="取消選取", exact=True).click()
            report["steps"].append(
                "Batch selection, time shift, court change and undo leave persisted data unchanged"
            )
            stage(a.match_id, a)
            preview()
            expect(page.locator("#confirm")).to_be_enabled()
            expect(page.locator(".schedule-conflicts.resolved")).to_be_visible()
            expect(page.locator(".schedule-change")).to_have_count(1)
            assert repo.load().id == old_revision
            with page.expect_download() as download:
                page.get_by_role("button", name="下載異動草案", exact=True).click()
            download.value.save_as(str(output / "draft.txt"))
            draft = (output / "draft.txt").read_text("utf-8")
            assert "草案" in draft and "尚未確認" in draft
            assert "PRIVATE-TEST" not in draft
            screenshots("resolved-preview")
            page.locator("#confirm").click()
            expect(page.locator("#review")).not_to_be_visible()
            expect(page.locator("#message")).to_have_text("已儲存新版本。")
            expected_court = next(
                c["label"] for c in original.config["courts"] if c["id"] == a.court_id
            )
            expect(reader.locator(f'[data-match-id="{a.match_id}"] .match-footer')).to_contain_text(
                expected_court, timeout=15000
            )
            assert repo.load().id != old_revision
            report["steps"].append(
                "Repair preview resolves conflicts, leaves source unchanged until confirm, reader updates after confirm"
            )
            page.reload()
            expect(page.locator(".schedule-history")).to_have_count(1)
            page.locator(".schedule-recent > summary").click()
            page.locator(".schedule-history > summary").first.click()
            with page.expect_download() as download:
                page.get_by_role("button", name="下載當次異動公告", exact=True).click()
            download.value.save_as(str(output / "applied-notice.txt"))
            notice = (output / "applied-notice.txt").read_text("utf-8")
            assert "公告" in notice and "套用版本" in notice
            assert "PRIVATE-TEST" not in notice
            screenshots("saved-history")
            report["steps"].append(
                "Persisted review survives reload and announcement omits private actor/reason"
            )
            stage(a.match_id, b)
            preview()
            expect(page.locator("#confirm")).to_be_disabled()
            expect(page.locator(".schedule-conflicts.introduced")).to_be_visible()
            blocked_base = repo.load().id
            rejected = page.evaluate(
                "async()=>{const r=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json','X-Squash-Token':token},body:JSON.stringify({id:pending.id,hash:pending.hash})});return r.status}"
            )
            assert rejected >= 400 and repo.load().id == blocked_base
            screenshots("blocked-preview")
            page.locator("#cancel-review").click()
            expect(page.locator("#manual-preview")).to_be_enabled()
            # Atomic two-match swap: each move alone conflicts, the pair is valid.
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="清空草案", exact=True).click()
            for mid in (a.match_id, b.match_id):
                page.locator(f'.schedule-check[data-match-id="{mid}"]').check()
            page.locator("#batch-swap").click()
            preview()
            expect(page.locator("#confirm")).to_be_enabled()
            expect(page.locator(".schedule-change")).to_have_count(2)
            screenshots("batch-swap")
            with repo.lock():
                current = repo.load()
                repo.save(current, current.id)
            page.locator("#confirm").click()
            expect(page.locator("#message")).to_contain_text("原預覽不能套用")
            expect(page.locator("#manual-preview")).to_be_disabled()
            report["steps"].append(
                "Invalid drafts cannot be confirmed, two-match atomic swap is valid, stale base rejects and keeps draft for review"
            )
            browser.close()
        assert content_hash(original_repo.load()) == source_hash
        assert not report["errors"], report["errors"]
        report.update(status="passed", source_hash=source_hash, source_unchanged=True)
        return report
    finally:
        atomic_json(output / "report.json", report)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("output/operator-demo-final/junior"))
    args = parser.parse_args()
    print(check(args.output, args.source))
