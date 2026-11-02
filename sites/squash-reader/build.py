"""Export the existing spectator UI and allowlisted snapshot for static hosting."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from shutil import copyfile

from squash_scheduler.public_feed import PublicFeed
from squash_scheduler.storage.repository import Repository


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"Expected one source marker: {old[:60]}")
    return text.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    dist = root / "dist"
    assets = args.scheduler.resolve() / "src/squash_scheduler/assets"
    repo = Repository(args.workspace.resolve())
    feed = PublicFeed()
    payload = feed.read(repo)
    head = feed.version(repo)
    if payload["version"] != head["version"]:
        raise ValueError("Workspace changed during export; retry with a stable revision")
    head["snapshot_url"] = f"snapshots/{payload['version']}.json"
    (dist / "public/snapshots").mkdir(parents=True, exist_ok=True)
    for name in ("live.css", "live-source.mjs", "live-polygon.mjs"):
        copyfile(assets / name, dist / name)
    copyfile(assets / "fonts/IBMPlexSans.woff2", dist / "font.woff2")
    copyfile(assets / "fonts/OFL.txt", dist / "FONT-LICENSE.txt")
    document_path = root / "public-documents/manifest.json"
    document = json.loads(document_path.read_text()) if document_path.is_file() else None
    if document:
        filename = document["filename"]
        if Path(filename).name != filename:
            raise ValueError("PDF filename must be a basename")
        pdf = root / "public-documents" / filename
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != document["sha256"]:
            raise ValueError("Public PDF checksum mismatch")
        (dist / "downloads").mkdir(exist_ok=True)
        copyfile(pdf, dist / "downloads" / filename)
    html = (root / "reader.html").read_text(encoding="utf-8")
    if document:
        html = html.replace("event-booklet.pdf", document["filename"]).replace("PDF_PAGE_COUNT", str(document["pages"]))
    else:
        html = re.sub(r'<div class="pdf-download">.*?</small></div>', '', html, flags=re.S)
        html = re.sub(r'<a href="/downloads/event-booklet.pdf"[^>]*>.*?</a>', '', html, flags=re.S)
        html = html.replace('class="schedule-first"', 'class="schedule-first no-event-pdf"')
    copyfile(root / "experience.css", dist / "experience.css")
    copyfile(root / "board.css", dist / "board.css")
    copyfile(root / "mobile.css", dist / "mobile.css")
    copyfile(root / "reader-state.mjs", dist / "reader-state.mjs")
    script = (assets / "live.mjs").read_text(encoding="utf-8")
    script = replace_once(script, "hashView() || 'bracket'", "hashView() || 'schedule'")
    script = script.replace("即時賽況", "唯讀賽程").replace("即時積分與排名", "積分與排名")
    script = replace_once(script, "? '已同步'", "? '已取得網站資料'")
    script = replace_once(script, "最後成功同步 ${time(status.checkedAt.toISOString())} · 賽況更新", "本次讀取 ${time(status.checkedAt.toISOString())} · 資料更新")
    schedule_start = script.index("function schedule(matches) {")
    render_start = script.index("function render() {", schedule_start)
    script = script[:schedule_start] + (root / "experience.js").read_text(encoding="utf-8") + "\n" + script[render_start:]
    script = replace_once(script, "  const matches = visible();", "  syncBrowseControls();\n  const matches = visible();")
    script = replace_once(script, "$('reader-navigation').open = !compactNavigation.matches;", "$('reader-navigation').open = false;")
    script = "import {playerNames, matchesPlayer, queryState, stateSearch, orderedMatches} from './reader-state.mjs';\n" + script
    script = replace_once(script, "hashView() || 'schedule'", "hashView() || queryState(location.search).view")
    script = replace_once(script, "return data.matches.filter(m =>", "return data.matches.filter(m => (!selectedPlayer || matchesPlayer(m, selectedPlayer)) &&")
    script = replace_once(script, "  render();\n}\n\nfunction connection", "  prepareReaderState();\n  render();\n}\n\nfunction connection")
    script = replace_once(script, "function filterChanged() { limit = 40; history.replaceState(null,'',location.pathname+location.search); render(); }", "function filterChanged() { limit = 40; writeQueryState(); render(); }")
    script = replace_once(script, "$('search').addEventListener('input', filterChanged)", "$('search').addEventListener('input', searchChanged)")
    script = replace_once(script, "view = el.dataset.view; history.replaceState(null,'',location.pathname+location.search);", "view = el.dataset.view; writeQueryState();")
    script = replace_once(script, "function connection(status) {", "function connection(status) {\n  void refreshSyncStatus(status);")
    (dist / "index.html").write_text(html, encoding="utf-8")
    (dist / "live.mjs").write_text(script, encoding="utf-8")
    documents = {
        "public/version.json": head,
        f"public/{head['snapshot_url']}": payload,
        "live-config.json": {
            "source": {"type": "http-json", "version_url": "/public/version.json"},
            "poll_interval_ms": 10000,
        },
    }
    for name, value in documents.items():
        (dist / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (dist / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    print(json.dumps({"version": payload["version"], "matches": len(payload["matches"]), "divisions": len(payload["divisions"])}, indent=2))


if __name__ == "__main__":
    main()
