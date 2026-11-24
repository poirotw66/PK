"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Tournament, Match, Update } from "../lib/schedule/schema";
import { localTime, parseLocal, review } from "../lib/schedule/rules";
import { mergeDraft, withDraft } from "../lib/schedule/placements";
import LiveControl from "./live-control";
import ScheduleBoard from "./schedule-board";
import MobileSchedule from "./mobile-schedule";
import MatchSearch from "./match-search";
import { searchMatches } from "../lib/schedule/navigation";
import { useIsMobile } from "../hooks/use-mobile";
type SiteSignIn = { email: string; sign_out_url: string };
type Status = {
    imported: boolean;
    can_import: boolean;
    can_switch_draft: boolean;
    csrf: string;
    sign_in: SiteSignIn;
};
type Change = {
    id: string;
    code: string;
    before: Match["assignment"];
    after: Update;
};
type History = {
    id: string;
    actor: string;
    reason: string;
    created: number;
    changes: Change[];
};
type Issue = {
    key: string;
    message: string;
};
type State = {
    revision: string;
    state: Tournament;
    issues: Issue[];
    history: History[];
};
type Plan = {
    id: string | null;
    can_apply: boolean;
    changes: Change[];
    introduced: Issue[];
    remaining: Issue[];
};
export default function Console({ screen = "live" }: { screen?: "live" | "schedule" }) {
    const [status, setStatus] = useState<Status | null>(null), [data, setData] = useState<State | null>(null);
    const [blockedSignIn, setBlockedSignIn] = useState<SiteSignIn | null>(null);
    const [message, setMessage] = useState(""), [busy, setBusy] = useState(false), [success, setSuccess] = useState(false);
    const [day, setDay] = useState(""), [division, setDivision] = useState(""), [court, setCourt] = useState(""), [query, setQuery] = useState("");
    const [draft, setDraft] = useState<Record<string, Update>>({}), [reason, setReason] = useState("");
    const [editing, setEditing] = useState<Match | null>(null), [editTime, setEditTime] = useState(""), [editCourt, setEditCourt] = useState("");
    const [plan, setPlan] = useState<Plan | null>(null), [showHistory, setShowHistory] = useState(false);
    const editDialog = useRef<HTMLDialogElement>(null), reviewDialog = useRef<HTMLDialogElement>(null);
    const [focusRequest, setFocusRequest] = useState<{
        id: string;
        sequence: number;
    } | null>(null);
    const [viewMode, setViewMode] = useState<"board" | "list" | "timeline">("board");
    const [undoStack, setUndoStack] = useState<Record<string, Update>[]>([]);
    const isMobile = useIsMobile(761);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [reasonOpen, setReasonOpen] = useState(false);
    const reasonDialog = useRef<HTMLDialogElement>(null);
    useEffect(() => {
        if (reasonOpen && isMobile && reasonDialog.current && !reasonDialog.current.open)
            reasonDialog.current.showModal();
        else
            reasonDialog.current?.close();
    }, [reasonOpen, isMobile]);
    async function checkDraft() {
        const value = await api<Plan>("preview", { revision: data!.revision, updates: Object.values(draft), reason });
        reasonDialog.current?.close();
        setReasonOpen(false);
        setPlan(value);
    }
    const draftCount = Object.keys(draft).length;
    function locateMatch(match: Match) {
        if (!match.assignment)
            return;
        setDay(localTime(match.assignment.start).slice(0, 10));
        setCourt(match.assignment.court);
        setDivision("");
        setViewMode("board");
        setFocusRequest(previous => ({ id: match.id, sequence: (previous?.sequence ?? 0) + 1 }));
        if (isMobile)
            setFiltersOpen(false);
    }
    function changeDraft(next: Record<string, Update>) {
        if (JSON.stringify(next) === JSON.stringify(draft))
            return;
        setUndoStack(history => [...history, draft].slice(-20));
        setDraft(next);
        setPlan(null);
    }
    function addToDraft(update: Update) {
        if (data)
            changeDraft(mergeDraft(data.state, draft, [update]));
    }
    function addBatchToDraft(updates: Update[], focusId: string) {
        if (!data)
            return;
        const check = review(withDraft(data.state, draft), updates);
        if (!check.can_apply)
            throw new Error([...check.introduced, ...check.remaining][0]?.message ?? "交換有衝突，無法加入草案。");
        changeDraft(mergeDraft(data.state, draft, updates));
        const update = updates.find(row => row.id === focusId);
        const match = data.state.matches.find(row => row.id === focusId);
        if (update && match)
            locateMatch({ ...match, assignment: { start: update.start, court: update.court } });
    }
    function discardMatch(id: string) {
        const next = { ...draft };
        delete next[id];
        changeDraft(next);
    }
    function undoDraft() {
        const previous = undoStack.at(-1);
        if (!previous)
            return;
        setDraft(previous);
        setUndoStack(history => history.slice(0, -1));
        setPlan(null);
    }
    const api = useCallback(async <T = unknown,>(path: string, body?: unknown): Promise<T> => {
        const response = await fetch("/api/" + path, { method: body === undefined ? "GET" : "POST", credentials: "same-origin", headers: body === undefined ? {} : { "Content-Type": "application/json", "X-CSRF-Token": status?.csrf ?? "" }, body: body === undefined ? undefined : JSON.stringify(body) });
        const value = await response.json() as {
            error?: string;
            sign_in?: SiteSignIn;
        };
        if (!response.ok) {
            if (path === "status" && response.status === 403 && value.sign_in) {
                setBlockedSignIn(value.sign_in);
                setStatus(null);
            }
            if (response.status === 401) {
                setReasonOpen(false);
                setPlan(null);
                setEditing(null);
                setStatus(null);
            }
            throw new Error(value.error ?? "目前無法完成操作。");
        }
        return value as T;
    }, [status?.csrf]);
    async function load() {
        const next = await api<Status>("status");
        setBlockedSignIn(null);
        setStatus(next);
        if (next.imported) {
            const value = await api<State>("state");
            setData(value);
            setUndoStack([]);
            setDay(old => old || value.state.dates[0]);
        }
    }
    useEffect(() => { load().catch(e => setMessage(e.message)); }, []); // Initial state is always fetched from protected routes.
    useEffect(() => {
        const handler = (event: BeforeUnloadEvent) => {
            if (draftCount) {
                event.preventDefault();
                event.returnValue = "";
            }
        };
        window.addEventListener("beforeunload", handler);
        return () => window.removeEventListener("beforeunload", handler);
    }, [draftCount]);
    useEffect(() => {
        if (editing)
            editDialog.current?.showModal();
    }, [editing]);
    useEffect(() => {
        if (plan)
            reviewDialog.current?.showModal();
    }, [plan]);
    async function run(action: () => Promise<void>) {
        setBusy(true);
        setMessage("");
        setSuccess(false);
        try {
            await action();
        }
        catch (e) {
            setMessage(e instanceof Error ? e.message : "操作未完成。");
        }
        finally {
            setBusy(false);
        }
    }
    function openEditor(match: Match) { const a = draft[match.id] ?? match.assignment; setEditTime(a ? localTime(a.start) : `${day}T08:00`); setEditCourt(a?.court ?? data!.state.courts[0].id); setEditing(match); setPlan(null); }
    function saveDraft() {
        if (!editing || !data)
            return;
        try {
            const value = { id: editing.id, start: parseLocal(editTime), court: editCourt };
            addToDraft(value);
            setEditing(null);
            setPlan(null);
        }
        catch (e) {
            setMessage((e as Error).message);
        }
    }
    const [switchFile, setSwitchFile] = useState<File | null>(null);
    const state = data?.state;
    const courtName = (id: string) => state?.courts.find(c => c.id === id)?.name ?? id;
    const point = (a: Match["assignment"]) => a ? `${localTime(a.start).replace("T", " ")} · ${courtName(a.court)}` : "未排程";
    const error = message ? <div className={"notice " + (success ? "success" : "error")} role="status">{message}</div> : null;
    const signIn = blockedSignIn ?? status?.sign_in;
    const switchAccount = signIn && <p>目前登入身分為 <strong>{signIn.email}</strong>。 <a href={signIn.sign_out_url} target="_top">重新登入</a></p>;
    const header = <><header className={status && viewMode !== "list" ? "compact-header" : undefined}><a className="wordmark" href="/">PK<span>賽程管理台</span></a><div className="header-right"><span className="private-badge">私人管理後台</span>{status && <><span className="account">{status.sign_in.email}</span><button disabled={busy || draftCount > 0} title={draftCount ? "請先儲存或清空草案" : ""} onClick={() => run(async () => { await api("logout", {}); window.open(status.sign_in.sign_out_url, "_top"); })}>登出</button></>}</div></header>{status && <nav className="admin-navigation" aria-label="管理功能"><a href="/live" aria-current={screen === "live" ? "page" : undefined} aria-disabled={busy || draftCount > 0} onClick={event => { if (busy || draftCount) { event.preventDefault(); setMessage("請先儲存或清空草案，再切換頁面。"); } }}><strong>現場計分</strong><span>開賽與記分</span></a><a href="/schedule" aria-current={screen === "schedule" ? "page" : undefined}><strong>賽程調整</strong><span>調整與發布</span></a></nav>}</>;
    if (blockedSignIn)
        return <>{header}<main className="onboarding"><h1>此帳號沒有管理權限</h1>{switchAccount}<p>請改用已獲授權的 ChatGPT 帳號登入管理台。</p></main></>;
    if (!status)
        return <>{header}<main className="onboarding"><h1>正在開啟操作台</h1>{error}{!message && <p>安全連線至賽程資料庫…</p>}{message && <><button onClick={() => run(load)}>重新連線</button> <a href="/temporary-login" target="_top">重新登入</a></>}</main></>;
    if (!status.imported)
        return <>{header}<main className="onboarding"><p className="eyebrow">SCHEDULE OPERATIONS</p><h1>賽程匯入</h1><p className="intro">賽程集中保存，調整前先檢查，確認後才更新。</p>{error}
    {switchAccount}
    {status.can_import && <section className="import-panel"><span className="step">匯入既有賽程</span><h2>從本機帶入已確認的版本</h2><p>使用專案匯出的賽程 JSON，資料會存進私人資料庫。此版本只允許首次匯入。</p><label className="file-label">選擇賽程 JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={event => {
                    const file = event.target.files?.[0];
                    if (!file)
                        return;
                    run(async () => {
                        if (file.size > 1024 * 1024)
                            throw new Error("檔案不得超過 1 MB。");
                        await api("import", JSON.parse(await file.text()));
                        await load();
                    });
                }}/></label></section>}
    {!status.can_import && <p>請等待原管理員匯入賽程。</p>}
    </main></>;
    if (!state)
        return <>{header}<main className="onboarding">{error}<p>正在載入賽程…</p><button onClick={() => run(load)}>重新載入</button></main></>;
    if (screen === "live") return <>{header}<main className="workspace live-workspace"><section className="heading"><div><h1>現場計分</h1><p>{state.name}</p><p>依場號或選手找比賽，確認開賽時再選空場。</p><a href="/ipad">舊款 iPad 操作頁</a></div><button disabled={busy} onClick={() => run(load)}>重新整理</button></section>{error}<LiveControl mode="live" revision={data!.revision} draftCount={0} api={api} onChanged={load}/></main></>;
    const divisions = [...new Set(state.matches.map(m => m.division))];
    const searchIds = new Set(searchMatches(state, draft, query).map(match => match.id));
    const filtered = state.matches.filter(m => { const a = draft[m.id] ?? m.assignment; return (!day || a && localTime(a.start).startsWith(day)) && (!division || m.division === division) && (!court || a?.court === court) && (!query.trim() || searchIds.has(m.id)); }).sort((a, b) => (draft[a.id]?.start ?? a.assignment?.start ?? Infinity) - (draft[b.id]?.start ?? b.assignment?.start ?? Infinity) || courtName(draft[a.id]?.court ?? a.assignment?.court ?? "").localeCompare(courtName(draft[b.id]?.court ?? b.assignment?.court ?? "")));
    return <>{header}<main className={"workspace" + (viewMode !== "list" ? " board-workspace" : "")}><section className="heading"><div><p className="eyebrow">SCHEDULE OPERATIONS</p><h1>賽程調整</h1><p>{state.name}</p><p>點選比賽，再點綠色空檔；多場調整最後一起確認儲存。</p><a className="publication-jump" href="#schedule-publication">預覽與發布賽程 ↓</a></div><button disabled={busy || draftCount > 0} onClick={() => run(load)}>重新整理</button></section>{error}
    {status.can_switch_draft && <section className="import-panel"><h2>切換到 226 場暫定賽程</h2><p>請選擇與公開站相同的賽程 JSON。切換後，新版比分會從零開始，還需要發布新版賽程才能同步。</p><label className="file-label">選擇 226 場賽程 JSON<input type="file" accept=".json,application/json" disabled={busy || draftCount > 0} onChange={event => setSwitchFile(event.target.files?.[0] ?? null)}/></label><button className="primary" disabled={!switchFile || busy || draftCount > 0} onClick={() => {
        if (!switchFile || !data) return;
        run(async () => {
            if (switchFile.size > 512 * 1024) throw new Error("檔案不得超過 512 KB。");
            await api("switch-draft", { base_revision: data.revision, payload: await switchFile.text() });
            setSwitchFile(null);
            await load();
            setSuccess(true);
            setMessage("已切換到 226 場暫定賽程。請發布新版賽程以恢復同步。");
        });
    }}>確認切換到 226 場</button></section>}
    <div className="policy"><span>08:00–17:30 開賽</span><span>賽後休息 ≥60 分鐘</span><span>每人每日 ≤5 場</span><span>只檢查已確定選手</span></div>
    {data!.issues.length > 0 && <div className="notice error">目前賽程有 {data!.issues.length} 項衝突，請先修正後再儲存。{data!.issues.map(i => <p key={i.key}>{i.message}</p>)}</div>}
    <section className="schedule-sheet"><div className="sheet-top"><div className="day-tabs" aria-label="比賽日期">{state.dates.map(d => <button key={d} className={day === d ? "selected" : ""} aria-pressed={day === d} onClick={() => setDay(d)}>{d.slice(5).replace("-", "/")}<small>{state.matches.filter(m => localTime((draft[m.id] ?? m.assignment)?.start ?? 0).startsWith(d)).length} 場</small></button>)}</div><button aria-pressed={showHistory} onClick={() => setShowHistory(!showHistory)}>異動紀錄</button></div>
    {isMobile && <div className="mobile-agenda-search"><MatchSearch state={state} draft={draft} query={query} onQuery={setQuery} onLocate={locateMatch}/></div>}
    <button className="mobile-filter-toggle" aria-expanded={filtersOpen} aria-controls="schedule-filters" onClick={() => setFiltersOpen(!filtersOpen)}>組別與場地篩選{query || division ? " · 已套用" : ""}<span>{filtersOpen ? "收合 ↑" : "展開 ↓"}</span></button>
    <div id="schedule-filters" className={"filters" + (filtersOpen ? " is-open" : "")}>{!isMobile && <MatchSearch state={state} draft={draft} query={query} onQuery={setQuery} onLocate={locateMatch}/>}<label>組別<select value={division} onChange={e => setDivision(e.target.value)}><option value="">全部組別</option>{divisions.map(d => <option key={d}>{d}</option>)}</select></label><label>場地<select value={court} onChange={e => setCourt(e.target.value)}><option value="">全部場地</option>{state.courts.map(c => <option value={c.id} key={c.id}>{c.name}</option>)}</select></label><p>{filtered.length} 場符合條件</p></div>
    {showHistory && <section className="history"><h2>最近 30 筆版本紀錄</h2>{data!.history.map(row => <details key={row.id}><summary>{new Date(row.created).toLocaleString("zh-TW")} · {row.reason.startsWith("Live ") ? "場地狀態更新" : `${row.changes.length} 場調整`} · {row.actor}</summary><p>{row.reason}</p>{row.changes.map(c => <p key={c.id}>第 {c.code} 場：{point(c.before)} → {point(c.after)}</p>)}<small>版本 {row.id.slice(0, 8)}</small></details>)}</section>}
    <div className="view-toolbar"><div className="view-switch" aria-label="賽程顯示方式"><button aria-pressed={viewMode === "board"} onClick={() => setViewMode("board")}>{isMobile ? "場次卡片" : "場地空檔表"}</button>{isMobile && <button aria-pressed={viewMode === "timeline"} onClick={() => setViewMode("timeline")}>時間表</button>}<button aria-pressed={viewMode === "list"} onClick={() => setViewMode("list")}>{isMobile ? "逐場編輯" : "場次清單"}</button></div><span>{isMobile && viewMode === "board" ? "點選場次卡片，就能挑選符合限制的空檔" : viewMode !== "list" ? "篩選外場次仍顯示，場地占用不會被隱藏" : "逐場檢視與手動指定時間"}</span></div>
    {isMobile && viewMode === "board" ? <MobileSchedule state={state} draft={draft} day={day} court={court} matches={filtered} busy={busy} onMove={addToDraft} onEdit={openEditor} onDiscard={discardMatch} onCourtChange={setCourt} onDayChange={setDay} onFocusHandled={() => setFocusRequest(null)} focusRequest={focusRequest} onBatchMove={addBatchToDraft} onLocate={locateMatch}/> : viewMode !== "list" ? <ScheduleBoard state={state} draft={draft} day={day} court={court} matches={filtered} busy={busy} onMove={addToDraft} onEdit={openEditor} onDiscard={discardMatch} onCourtChange={setCourt} focusRequest={focusRequest} onBatchMove={addBatchToDraft} onLocate={locateMatch}/> : <>
    <div className="list-head"><span>開賽／場地</span><span>對戰與組別</span><span>操作</span></div><div className="match-list">{filtered.map(m => { const a = draft[m.id] ?? m.assignment, changed = !!draft[m.id], locked = ["completed", "in_progress"].includes(m.status) || m.lock_time && m.lock_court; return <article className={"match-row" + (m.status === "completed" ? " is-completed" : m.status === "in_progress" ? " is-playing" : "") + (changed ? " changed" : "")} key={m.id}><div className="match-time"><strong>{a ? localTime(a.start).slice(11) : "未排程"}</strong><span>{a ? courtName(a.court) : "待安排"}</span></div><div className="match-info"><div className="match-meta"><span>第 {m.code} 場</span><span>{m.division} · {m.stage}</span>{changed && <b>草案</b>}</div><h2>{m.side_a}<span className="versus">對</span>{m.side_b}</h2>{changed && <p className="before">原：{point(m.assignment)}</p>}</div><button disabled={busy || locked} onClick={() => openEditor(m)}>{locked ? m.status === "completed" ? "已完成" : m.status === "in_progress" ? "比賽中" : "已鎖定" : changed ? "編輯草案" : "調整"}</button></article>; })}{!filtered.length && <div className="empty"><h2>沒有符合條件的場次</h2><p>試著清除搜尋、組別或場地篩選。</p><button onClick={() => { setQuery(""); setCourt(""); setDivision(""); }}>清除篩選</button></div>}</div></>}
    </section>
    <LiveControl mode="publication" revision={data!.revision} draftCount={draftCount} api={api} onChanged={load}/>
    <footer className="revision">版本 {data!.revision.slice(0, 8)} · Asia/Taipei · 修改會保留版本與異動原因。<span>儲存後請發布賽程；場地開賽與結束會自動同步。</span></footer>
  </main><aside className={"draft-bar" + (draftCount ? " has-draft" : " is-empty")} aria-label="賽程草案"><div><strong>{draftCount} 場待儲存</strong><span>草案只在目前頁面，尚未更新正式賽程。</span></div><label>異動原因<input maxLength={500} placeholder="例如：選手報到延誤，調整場次" value={reason} disabled={busy} onChange={e => { setReason(e.target.value); setPlan(null); }}/></label><div className="draft-actions"><button className="draft-undo" disabled={busy || !undoStack.length} onClick={undoDraft}><span className="desktop-label">復原上一步</span><span className="mobile-label">復原</span></button><button className="draft-clear" disabled={busy || !draftCount} onClick={() => changeDraft({})}>清空草案</button><button className="primary desktop-review" disabled={busy || !draftCount || !reason.trim()} onClick={() => run(checkDraft)}>{busy ? "檢查中…" : `檢查 ${draftCount} 場變更`}</button><button className="primary mobile-review-trigger" disabled={busy || !draftCount} onClick={() => setReasonOpen(true)}>檢查草案 →</button></div></aside>
  <dialog ref={reasonDialog} className="draft-review-dialog" aria-labelledby="draft-review-title" onCancel={event => {
            event.preventDefault();
            if (!busy)
                setReasonOpen(false);
        }}><form onSubmit={event => { event.preventDefault(); run(checkDraft); }}><div className="sheet-dialog-heading"><h2 id="draft-review-title">檢查 {draftCount} 場調整</h2><button type="button" disabled={busy} aria-label="關閉草案檢查" onClick={() => setReasonOpen(false)}>×</button></div><p className="dialog-description">先確認草案與異動原因，再檢查整份賽程。</p>{error}<div className="mobile-draft-changes">{Object.values(draft).map(update => { const match = state.matches.find(row => row.id === update.id)!; return <article key={update.id}><strong>第 {match.code} 場 · {match.side_a} 對 {match.side_b}</strong><p>{point(match.assignment)}</p><p className="proposed">→ {point(update)}</p></article>; })}</div><label>異動原因<input autoFocus required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} placeholder="例如：選手報到延誤，調整場次" disabled={busy}/></label><div className="dialog-actions"><button type="button" disabled={busy} onClick={() => { changeDraft({}); setReasonOpen(false); }}>清空草案</button><button className="primary" disabled={busy || !reason.trim()}>{busy ? "檢查中…" : "檢查衝突"}</button></div></form></dialog>
  {editing && <dialog ref={editDialog} onCancel={event => {
                event.preventDefault();
                if (!busy)
                    setEditing(null);
            }}><form onSubmit={event => { event.preventDefault(); saveDraft(); }}><p className="eyebrow">EDIT MATCH</p><h2>調整第 {editing.code} 場</h2><p className="dialog-description">{editing.side_a} 對 {editing.side_b}</p><label>開賽時間（台灣）<input autoFocus type="datetime-local" required value={editTime} disabled={editing.lock_time} step={state.grid * 60} onChange={e => setEditTime(e.target.value)}/></label><label>場地<select value={editCourt} disabled={editing.lock_court} onChange={e => setEditCourt(e.target.value)}>{state.courts.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><p className="footnote">加入草案後，還需要檢查並確認儲存。</p><div className="dialog-actions"><button type="button" onClick={() => setEditing(null)}>取消</button><button className="primary">加入草案</button></div></form></dialog>}
  {plan && <dialog ref={reviewDialog} onCancel={event => {
                event.preventDefault();
                if (!busy)
                    setPlan(null);
            }}><h2>{plan.can_apply ? "檢查通過，確認這些調整" : "發現衝突，請先調整草案"}</h2><p className="dialog-description">{plan.changes.length} 場變更 · {reason}</p>{error}{[...plan.introduced, ...plan.remaining].map(i => <p className="conflict" key={i.key}>{i.message}</p>)}<div className="review-changes">{plan.changes.map(c => <article key={c.id}><strong>第 {c.code} 場</strong><p>{point(c.before)}</p><p className="proposed">→ {point(c.after)}</p></article>)}</div><div className="dialog-actions"><button disabled={busy} onClick={() => setPlan(null)}>返回草案</button><button className="primary" disabled={busy || !plan.can_apply} onClick={() => run(async () => { await api("confirm", { id: plan.id }); setPlan(null); setDraft({}); setUndoStack([]); setReason(""); await load(); setSuccess(true); setMessage("已儲存新版本。"); })}>{busy ? "儲存中…" : "確認儲存"}</button></div></dialog>}
  </>;
}
