"""Export the existing spectator UI and allowlisted snapshot for static hosting."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from shutil import copyfile

from squash_scheduler.domain.rankings import effective_rules
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
    copyfile(root / "readability.css", dist / "readability.css")
    copyfile(root / "board.css", dist / "board.css")
    copyfile(root / "mobile.css", dist / "mobile.css")
    copyfile(root / "reader-state.mjs", dist / "reader-state.mjs")
    copyfile(root / "schedule-changes.mjs", dist / "schedule-changes.mjs")
    copyfile(root / "live-scores.mjs", dist / "live-scores.mjs")
    copyfile(root / "reader-pages.mjs", dist / "reader-pages.mjs")
    copyfile(root / "pages.css", dist / "pages.css")
    copyfile(root / "standings.mjs", dist / "standings.mjs")
    snapshot = repo.load()
    if snapshot.id != payload["revision_id"]:
        raise ValueError("Ranking rules changed during export")
    rule_fields = ("scoring", "walkover_rule", "retirement_rule", "double_forfeit_rule", "ranking_denominator_rule", "points_to_win_game", "win_by_points")
    ranking_rules = {"workspace_id": payload["workspace_id"], "stages": {}}
    for stage in snapshot.stages:
        if stage.type == "round_robin":
            rules = effective_rules(snapshot, stage.id)
            ranking_rules["stages"][stage.id] = {key: rules[key] for key in rule_fields if key in rules}
    (dist / "ranking-rules.mjs").write_text("export const rankingRules = " + json.dumps(ranking_rules, ensure_ascii=False) + ";\n", encoding="utf-8")
    script = (assets / "live.mjs").read_text(encoding="utf-8")
    script = replace_once(script, "hashView() || 'bracket'", "hashView() || 'schedule'")
    script = script.replace("即時賽況", "唯讀賽程").replace("即時積分與排名", "積分與排名")
    script = replace_once(script, "? '已同步'", "? '已取得網站資料'")
    script = replace_once(script, "最後成功同步 ${time(status.checkedAt.toISOString())} · 賽況更新", "本次讀取 ${time(status.checkedAt.toISOString())} · 資料更新")
    script = replace_once(script, "  if (group.advancement_count)", "  const pendingCloudStats = group.notice.startsWith('雲端成績已更新；');\n  if (pendingCloudStats) root.append(node('p','選手名單保留顯示；「—」表示統計待核定，逐場比分可在晉級對戰的「對戰與成績」查看。','ranking-pending'));\n  if (group.advancement_count)")
    script = replace_once(script, "row.qualifies ? 'qualifier' : ''", "row.qualifies && !pendingCloudStats ? 'qualifier' : ''")
    script = replace_once(script, "if (row.qualifies) name.append", "if (row.qualifies && !pendingCloudStats) name.append")
    script = replace_once(script, "[row.rank ?? (group.status === 'manual_review' ? '待核定' : '—'), row.points,row.wins,row.losses,row.forfeits,row.games_for,row.games_against].forEach", "(pendingCloudStats ? ['待核定','—','—','—','—','—','—'] : [row.rank ?? (group.status === 'manual_review' ? '待核定' : '—'), row.points,row.wins,row.losses,row.forfeits,row.games_for,row.games_against]).forEach")
    script = replace_once(script, "  data = next;", "  data = recalculateStandings(next, rankingRules);")
    script = replace_once(script, "['選手','名次','積分','勝','敗','棄／退','勝局','負局']", "['選手',group.status === 'provisional' ? '暫列名次' : '名次','積分','勝','敗','棄／退','勝局','負局']")
    script = replace_once(script, "String(value),'num'", "String(value) + (i === 0 && row.rank !== null && group.tie_groups.some(tie => tie.includes(row.entry_id)) ? '（並列）' : ''),'num'")
    script = replace_once(script, ".forEach(value => tr.append", ".forEach((value,i) => tr.append")
    script = "import {recalculateStandings} from './standings.mjs';\nimport {rankingRules} from './ranking-rules.mjs';\n" + script
    script = "import { visibleLiveScore, validLiveScore } from './live-scores.mjs';\n" + script
    script = replace_once(script, "  $('content').replaceChildren(content);", "  $('content').replaceChildren(content);\n  paintLiveScores();")
    schedule_start = script.index("function schedule(matches) {")
    render_start = script.index("function render() {", schedule_start)
    script = script[:schedule_start] + (root / "experience.js").read_text(encoding="utf-8") + "\n" + script[render_start:]
    script = replace_once(script, "const viewNames = {", "const viewNames = {courts:'各場地賽況',")
    script = replace_once(script, "(bracket|standings|schedule)", "(bracket|standings|schedule|courts)")
    script = replace_once(
        script,
        "${time(match.planned_start_at)} · ${label('courts', match.court_id)}",
        "${match.planned_start_at ? time(match.planned_start_at) + '（僅供參考）' : '未排程'}"
        "${match.status === 'in_progress' && match.court_id ? ' · ' + label('courts', match.court_id) : ''}",
    )
    script = replace_once(script, "    (!$('court').value || m.court_id === $('court').value) &&\n", "")
    script = replace_once(script, "  options('court', data.courts.map(c => [c.id, c.name]), '全部場地');\n", "")
    script = script.replace("可調整組別、搜尋、日期或場地。", "可調整組別、搜尋或日期。")
    script = replace_once(script, "['division', 'date', 'court']", "['division', 'date']")
    script = replace_once(script, "  if (match.delay_active)", "  if (match.status === 'completed' && !match.result) return '已結束・成績待登錄';\n  if (match.delay_active)")
    script = replace_once(script, "  const matches = visible();", "  syncBrowseControls();\n  const matches = visible();")
    script = replace_once(script, "$('reader-navigation').open = !compactNavigation.matches;", "$('reader-navigation').open = false;")
    script = "import {SeenSchedules, playerSchedule, changeDescription} from './schedule-changes.mjs';\n" + script
    script = "import {playerNames, matchesPlayer, queryState, stateSearch, orderedMatches, courtMatches} from './reader-state.mjs';\n" + script
    script = replace_once(script, "hashView() || 'schedule'", "hashView() || queryState(location.search).view")
    script = replace_once(script, "return data.matches.filter(m =>", "return data.matches.filter(m => (!selectedPlayer || matchesPlayer(m, selectedPlayer)) &&")
    script = replace_once(script, "  render();\n}\n\nfunction connection", "  prepareReaderState();\n  render();\n}\n\nfunction connection")
    script = replace_once(script, "function filterChanged() { limit = 40; history.replaceState(null,'',location.pathname+location.search); render(); }", "function filterChanged() { limit = 40; writeQueryState(); render(); }")
    script = replace_once(script, "$('search').addEventListener('input', filterChanged)", "$('search').addEventListener('input', searchChanged)")
    script = replace_once(script, "view = el.dataset.view; history.replaceState(null,'',location.pathname+location.search);", "view = el.dataset.view; writeQueryState();")
    script = replace_once(script, "function connection(status) {", "function connection(status) {\n  void refreshSyncStatus(status);")
    script = "import {pageInfo, readerPage, pageHref, pageForView} from './reader-pages.mjs';\n" + script
    script = replace_once(script, "let data = null, view = hashView() || queryState(location.search).view, limit = 40, reader;", "let page = readerPage(location.pathname, location.search, location.hash);\nlet data = null, view = pageInfo[page].view, limit = 40, reader;")
    script = replace_once(script, "  document.title = data.name + ' · 唯讀賽程';", "  document.title = data.name + ' · ' + pageInfo[page].title;")
    script = replace_once(script, "  syncBrowseControls();", "  syncBrowseControls();\n  if (page === 'live') { $('content').replaceChildren(); return; }\n  if (['bracket','standings'].includes(page) && !$('division').value) { $('summary').textContent = '共 ' + data.divisions.length + ' 個組別'; $('content').replaceChildren(panel('請先選擇組別', '選好組別後，就會顯示這個組別的' + (page === 'bracket' ? '晉級對戰。' : '積分與排名。'))); return; }")
    script = replace_once(script, "options('division', data.divisions.map(d => [d.id, d.name]), '全部組別');", "options('division', data.divisions.map(d => [d.id, d.name]), ['bracket','standings'].includes(page) ? '請選擇組別' : '全部組別');")
    script = replace_once(script, "document.querySelectorAll('[data-view]').forEach(el => el.addEventListener('click', () => { view = el.dataset.view; writeQueryState(); render(); }));", "")
    script = replace_once(script, "  document.querySelectorAll('[data-view]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.view === view)));", "")
    script = replace_once(script, "{ view = nextView; pendingHash = true; render(); }", "{ view = nextView; page = pageForView(view); history.replaceState(null,'',pageHref(page,location.search)+location.hash); pendingHash = true; render(); }")
    (dist / "index.html").write_text(html, encoding="utf-8")
    (dist / "pages").mkdir(exist_ok=True)
    for page, title in {"live":"即時賽況", "schedule":"查詢賽程", "bracket":"晉級對戰", "standings":"積分排名"}.items():
        document = html.replace('data-page="live"', f'data-page="{page}"').replace('<h1 id="hero-title">即時賽況</h1>', f'<h1 id="hero-title">{title}</h1>')
        (dist / "pages" / (page + ".html")).write_text(document, encoding="utf-8")
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
