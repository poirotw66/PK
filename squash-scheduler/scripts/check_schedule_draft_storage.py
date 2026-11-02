"""Real-browser rehearsal of private draft persistence and workspace/version protection."""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from squash_scheduler.domain.models import content_hash
from squash_scheduler.operator_server import create_server
from squash_scheduler.storage.repository import Repository, atomic_json


def check(output: Path, source: Path) -> dict:
    if output.exists():
        raise FileExistsError("Choose a fresh output directory")
    original = Repository(source)
    snapshot = original.load()
    source_hash = content_hash(snapshot)
    repo = Repository(output / "workspace")
    with repo.lock():
        repo.save(snapshot.model_copy(deep=True), None)
    server = create_server(repo, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    report = {"status": "running", "steps": [], "screens": [], "errors": [], "dialogs": []}
    atomic_json(output / "report.json", report)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()

            def observe(tab):
                tab.on("pageerror", lambda error: report["errors"].append(str(error)))

                def dialog(value):
                    report["dialogs"].append(value.type)
                    value.accept()

                tab.on("dialog", dialog)

            observe(page)
            page.goto(url + "/#schedule")
            expect(page.locator("#title")).to_have_text("賽程調整")
            assert page.evaluate("!!navigator.locks?.request")
            page.locator("#actor").fill("PRIVATE-ACTOR-NOT-STORED")
            key = page.evaluate("scheduleStorageState.store.key")
            revision = repo.load().id

            def saved(tab=page):
                expect(tab.locator("#schedule-storage-status")).to_contain_text("已自動保存")

            def shift(tab=page, minutes=15):
                tab.locator(".schedule-check:not(:disabled)").first.check()
                tab.locator("#batch-minutes").fill(str(minutes))
                tab.locator("#batch-shift").click()

            def clear(tab=page):
                tab.get_by_role("button", name="清空草案", exact=True).click()
                expect(tab.locator("#manual-preview")).to_be_disabled()
                tab.wait_for_function("()=>scheduleStorageState.store.peek()===null")

            shift()
            page.locator("#schedule-draft-details").click()
            page.locator("#manual-reason").fill("演練：調整場地作業時間")
            page.get_by_role("button", name="完成", exact=True).click()
            saved()
            raw = page.evaluate("localStorage.getItem(scheduleStorageState.store.key)")
            assert "PRIVATE-ACTOR" not in raw and "token" not in raw
            assert repo.load().id == revision
            page.reload()
            expect(page.locator("#schedule-draft-restore")).to_be_enabled()
            expect(page.locator("#manual-preview")).to_be_disabled()
            count = len(report["dialogs"])
            page.reload()
            expect(page.locator("#schedule-draft-restore")).to_be_enabled()
            assert len(report["dialogs"]) == count, "Unrestored storage must not warn on leaving"
            page.locator("#schedule-draft-restore").click()
            saved()
            page.locator("#schedule-draft-details").click()
            expect(page.locator("#manual-reason")).to_have_value("演練：調整場地作業時間")
            page.get_by_role("button", name="完成", exact=True).click()
            assert report["dialogs"].count("beforeunload") >= 1
            clear()
            page.locator("#schedule-undo").click()
            saved()
            assert (
                json.loads(page.evaluate("scheduleStorageState.store.peek()"))["updates"]
                == json.loads(raw)["updates"]
            )
            # An already open draft may outlive its match graph after a different operation.
            page.evaluate(
                "()=>{window.previousDraftData=data;const id=[...scheduleDraft.keys()][0];data={...data,revision_id:'changed-client-version',matches:data.matches.filter(m=>m.id!==id)};render()}"
            )
            page.locator("#schedule-draft-details").click()
            expect(page.get_by_role("dialog", name="待套用變更")).to_contain_text(
                "來源場次已不在目前版本"
            )
            page.get_by_role("button", name="完成", exact=True).click()
            page.evaluate(
                "()=>{data=window.previousDraftData;delete window.previousDraftData;render()}"
            )
            report["steps"].append(
                "Autosave, explicit reload recovery, private fields and leave warning"
            )
            report["steps"].append(
                "Undo after clearing re-saves the draft; removed source matches remain safely viewable"
            )

            with repo.lock():
                current = repo.load()
                repo.save(current, current.id)
            page.reload()
            expect(page.locator("#schedule-draft-restore")).to_be_disabled()
            page.get_by_role("button", name="查看已保存草案", exact=True).click()
            expect(page.get_by_role("dialog", name="已保存的賽程草案")).to_contain_text(
                "不可直接套用"
            )
            page.get_by_role("button", name="完成", exact=True).click()
            page.evaluate(
                "()=>{const s=scheduleStorageState.store,r=JSON.parse(localStorage.getItem(s.key));r.updates[0].match_id='missing-source-match';localStorage.setItem(s.key,JSON.stringify(r))}"
            )
            page.reload()
            expect(page.locator("#schedule-draft-restore")).to_be_disabled()
            page.get_by_role("button", name="查看已保存草案", exact=True).click()
            expect(page.get_by_role("dialog", name="已保存的賽程草案")).to_contain_text(
                "來源場次已不在目前版本"
            )
            page.get_by_role("button", name="完成", exact=True).click()
            page.get_by_role("button", name="丟棄已保存草案", exact=True).click()
            page.wait_for_function("()=>scheduleStorageState.store.peek()===null")
            page.evaluate("()=>localStorage.setItem(scheduleStorageState.store.key,'{corrupt')")
            page.reload()
            page.get_by_role("button", name="丟棄損壞的草案", exact=True).click()
            page.wait_for_function("()=>scheduleStorageState.store.peek()===null")
            report["steps"].append(
                "Stale and missing-source drafts remain read-only; corrupt record can be discarded"
            )

            other = context.new_page()
            observe(other)
            other.goto(url + "/#schedule")
            expect(other.locator("#title")).to_have_text("賽程調整")
            shift()
            saved()
            expect(other.locator("#schedule-draft-restore")).to_be_enabled()
            other.locator("#schedule-draft-restore").click()
            saved(other)
            expect(page.locator("#schedule-storage-status")).to_contain_text("其他分頁")
            foreign = page.evaluate("localStorage.getItem(scheduleStorageState.store.key)")
            page.locator("#batch-shift").click()
            assert page.evaluate("localStorage.getItem(scheduleStorageState.store.key)") == foreign
            page.screenshot(path=str(output / "cross-tab-protection.png"))
            report["screens"].append("cross-tab-protection.png")
            other.close(run_before_unload=True)
            page.get_by_role("button", name="清空草案", exact=True).click()
            expect(page.locator("#schedule-draft-restore")).to_be_enabled()
            assert page.evaluate("localStorage.getItem(scheduleStorageState.store.key)") == foreign
            page.get_by_role("button", name="丟棄已保存草案", exact=True).click()
            page.wait_for_function("()=>scheduleStorageState.store.peek()===null")
            report["steps"].append(
                "Two real tabs detect external writes and never silently overwrite or delete the other draft"
            )

            shift()
            saved()
            original_draft = page.evaluate("localStorage.getItem(scheduleStorageState.store.key)")
            secret = page.evaluate("token")
            metadata = {
                "revision_id": repo.load().id,
                "actor": "QA",
                "reason": "workspace isolation",
            }

            def request(path, body=None, binary=False, raw=False):
                headers = {"X-Squash-Token": secret}
                if binary:
                    headers.update(
                        {
                            "Content-Type": "application/zip",
                            "X-Restore-Metadata": base64.b64encode(
                                json.dumps(metadata).encode()
                            ).decode(),
                        }
                    )
                    payload = body
                elif body is not None:
                    headers["Content-Type"] = "application/json"
                    payload = json.dumps(body).encode()
                else:
                    payload = None
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
                connection.request(
                    "POST" if body is not None else "GET", path, body=payload, headers=headers
                )
                response = connection.getresponse()
                value = response.read()
                connection.close()
                assert response.status == 200, value.decode("utf-8", "replace")
                return value if raw else json.loads(value)

            backup = request("/api/backup", metadata)
            archive = request(backup["url"], raw=True)
            plan = request("/api/restore-preview", archive, binary=True)
            restored = request("/api/restore-confirm", {"id": plan["id"], "hash": plan["hash"]})
            page.evaluate("id=>previewSwitch(id,'隔離還原工作區')", restored["restore_id"])
            page.locator("#confirm").click()
            expect(page.locator("#review")).not_to_be_visible()
            page.wait_for_function(
                "old=>scheduleStorageState?.store?.key&&scheduleStorageState.store.key!==old",
                arg=key,
            )
            expect(page.locator("#manual-preview")).to_be_disabled()
            assert page.evaluate("key=>localStorage.getItem(key)", key) == original_draft
            child_key = page.evaluate("scheduleStorageState.store.key")
            shift(minutes=30)
            saved()
            child_draft = page.evaluate("localStorage.getItem(scheduleStorageState.store.key)")
            page.evaluate("()=>previewSwitch('original','原工作區')")
            page.locator("#confirm").click()
            expect(page.locator("#review")).not_to_be_visible()
            expect(page.locator("#schedule-draft-restore")).to_be_enabled()
            assert page.evaluate("key=>localStorage.getItem(key)", child_key) == child_draft
            page.locator("#schedule-draft-restore").click()
            saved()
            assert page.evaluate("JSON.stringify([...scheduleDraft.values()])") == json.dumps(
                json.loads(original_draft)["updates"], separators=(",", ":")
            )
            report["steps"].append(
                "Switching restored workspaces preserves independent drafts even when their revision IDs match"
            )

            clear()
            page.locator("#schedule-select-all").check()
            page.locator("#batch-minutes").fill("15")
            page.locator("#batch-shift").click()
            saved()
            page.locator("#manual-preview").click()
            expect(page.locator("#confirm")).to_be_enabled()
            page.locator("#confirm").click()
            expect(page.locator("#review")).not_to_be_visible()
            page.wait_for_function("()=>scheduleStorageState.store.peek()===null")
            assert page.evaluate("key=>localStorage.getItem(key)", child_key) == child_draft
            count = len(report["dialogs"])
            page.reload()
            expect(page.locator("#manual-preview")).to_be_disabled()
            expect(page.locator("#schedule-draft-restore")).to_have_count(0)
            assert len(report["dialogs"]) == count
            report["steps"].append(
                "Successful formal confirmation removes only its own draft and no longer warns on reload"
            )
            for width in (1440, 768, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                name = f"draft-status-{width}.png"
                page.screenshot(path=str(output / name))
                report["screens"].append(name)
            denied_context = browser.new_context(viewport={"width": 1440, "height": 1000})
            denied_context.add_init_script(
                "window.originalSetItem=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException('quota','QuotaExceededError')}"
            )
            denied = denied_context.new_page()
            observe(denied)
            denied.goto(url + "/#schedule")
            expect(denied.locator("#title")).to_have_text("賽程調整")
            shift(denied)
            expect(denied.locator("#schedule-storage-status")).to_contain_text("保存失敗")
            expect(denied.locator(".schedule-slot.is-changed")).to_have_count(1)
            denied.evaluate("()=>{Storage.prototype.setItem=window.originalSetItem}")
            denied.get_by_role("button", name="重試保存草案", exact=True).click()
            saved(denied)
            denied.close(run_before_unload=True)
            denied_context.close()
            report["steps"].append(
                "Quota failure remains editable, reports unsaved state and can retry successfully"
            )
            browser.close()
        assert not report["errors"], report["errors"]
        assert content_hash(original.load()) == source_hash
        report.update(status="passed", source_unchanged=True, source_hash=source_hash)
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
