"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Tournament, Match, Update } from "../lib/schedule/schema";
import { localTime, parseLocal, review } from "../lib/schedule/rules";
import { mergeDraft, withDraft } from "../lib/schedule/placements";
import LiveControl from "./live-control";
import ScheduleBoard from "./schedule-board";
import MatchSearch from "./match-search";
import { searchMatches } from "../lib/schedule/navigation";
import { useIsMobile } from "../hooks/use-mobile";
type Status = {
    configured: boolean;
    imported: boolean;
    authenticated: boolean;
    csrf?: string;
    username?: string;
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
export default function Console() {
    const [status, setStatus] = useState<Status | null>(null), [data, setData] = useState<State | null>(null);
    const [message, setMessage] = useState(""), [busy, setBusy] = useState(false), [success, setSuccess] = useState(false);
    const [username, setUsername] = useState(""), [password, setPassword] = useState(""), [confirmation, setConfirmation] = useState("");
    const [day, setDay] = useState(""), [division, setDivision] = useState(""), [court, setCourt] = useState(""), [query, setQuery] = useState("");
    const [draft, setDraft] = useState<Record<string, Update>>({}), [reason, setReason] = useState("");
    const [editing, setEditing] = useState<Match | null>(null), [editTime, setEditTime] = useState(""), [editCourt, setEditCourt] = useState("");
    const [plan, setPlan] = useState<Plan | null>(null), [showHistory, setShowHistory] = useState(false);
    const editDialog = useRef<HTMLDialogElement>(null), reviewDialog = useRef<HTMLDialogElement>(null);
    const [focusRequest, setFocusRequest] = useState<{
        id: string;
        sequence: number;
    } | null>(null);
    const [viewMode, setViewMode] = useState<"board" | "list">("board");
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
        };
        if (!response.ok) {
            if (response.status === 401 && status?.authenticated) {
                setReasonOpen(false);
                setPlan(null);
                setEditing(null);
                setStatus({ ...status, authenticated: false });
            }
            throw new Error(value.error ?? "目前無法完成操作。");
        }
        return value as T;
    }, [status?.csrf, status?.authenticated]);
    async function load() {
        const next = await api<Status>("status");
        setStatus(next);
        if (next.authenticated && next.imported) {
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
    const state = data?.state;
    const courtName = (id: string) => state?.courts.find(c => c.id === id)?.name ?? id;
    const point = (a: Match["assignment"]) => a ? `${localTime(a.start).replace("T", " ")} · ${courtName(a.court)}` : "未排程";
    const error = message ? <div className={"notice " + (success ? "success" : "error")} role="status">{message}</div> : null;
    const header = <header className={status?.authenticated && viewMode === "board" ? "compact-header" : undefined}><a className="wordmark" href="/">PK<span>賽程管理台</span></a><div className="header-right"><span className="private-badge">私人管理後台</span>{status?.authenticated && <><span className="account">{status.username}</span><button disabled={busy || draftCount > 0} title={draftCount ? "請先儲存或清空草案" : ""} onClick={() => run(async () => { await api("logout", {}); setData(null); await load(); })}>登出</button></>}</div></header>;
    if (!status)
        return <>{header}<main className="onboarding"><h1>正在開啟操作台</h1>{error}<p>安全連線至賽程資料庫…</p>{message && <button onClick={() => run(load)}>重新連線</button>}</main></>;
    if (!status.configured || !status.authenticated || !status.imported)
        return <>{header}<main className="onboarding"><p className="eyebrow">SCHEDULE OPERATIONS</p><h1>{!status.configured ? "設定你的操作台" : "管理員登入"}</h1><p className="intro">賽程集中保存，調整前先檢查，確認後才更新。</p>{error}
    {!status.imported && <section className="import-panel"><span className="step">01 · 匯入既有賽程</span><h2>從本機帶入已確認的版本</h2><p>使用專案匯出的賽程 JSON，資料會存進私人資料庫。此版本只允許首次匯入。</p><label className="file-label">選擇賽程 JSON<input type="file" accept=".json,application/json" disabled={busy || status.configured && !status.authenticated} onChange={event => {
                    const file = event.target.files?.[0];
                    if (!file)
                        return;
                    run(async () => {
                        if (file.size > 1024 * 1024)
                            throw new Error("檔案不得超過 1 MB。");
                        await api("import", JSON.parse(await file.text()));
                        await load();
                    });
                }}/></label>{status.configured && !status.authenticated && <p>請先登入後再匯入。</p>}</section>}
    {(!status.configured || !status.authenticated) && <form className="login-form" onSubmit={event => {
                    event.preventDefault();
                    run(async () => {
                        if (!status.configured && password !== confirmation)
                            throw new Error("兩次密碼不一致。");
                        await api(status.configured ? "login" : "setup", { username, password });
                        setPassword("");
                        setConfirmation("");
                        await load();
                    });
                }}><span className="step">{status.configured ? "管理員驗證" : "02 · 設定管理員帳密"}</span><label>帳號<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" pattern="[A-Za-z0-9_.-]{3,64}" minLength={3} maxLength={64} required/><small>3–64 字元，英文字母、數字、底線、句點或連字號。</small></label><label>密碼<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={status.configured ? "current-password" : "new-password"} minLength={status.configured ? 1 : 12} maxLength={256} required/></label>{!status.configured && <label>再次輸入密碼<input type="password" value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="new-password" minLength={12} required/><small>至少 12 字元；請使用此後台專用的密碼。</small></label>}<button className="primary" disabled={busy}>{busy ? "處理中…" : status.configured ? "登入操作台" : "設定帳密並登入"}</button></form>}
    <p className="footnote">Sites 平台登入保護已啟用。管理員密碼不會存進原始碼。</p></main></>;
    if (!state)
        return <>{header}<main className="onboarding">{error}<p>正在載入賽程…</p><button onClick={() => run(load)}>重新載入</button></main></>;
    const divisions = [...new Set(state.matches.map(m => m.division))];
    const searchIds = new Set(searchMatches(state, draft, query).map(match => match.id));
    const filtered = state.matches.filter(m => { const a = draft[m.id] ?? m.assignment; return (!day || a && localTime(a.start).startsWith(day)) && (!division || m.division === division) && (!court || a?.court === court) && (!query.trim() || searchIds.has(m.id)); }).sort((a, b) => (draft[a.id]?.start ?? a.assignment?.start ?? Infinity) - (draft[b.id]?.start ?? b.assignment?.start ?? Infinity) || courtName(draft[a.id]?.court ?? a.assignment?.court ?? "").localeCompare(courtName(draft[b.id]?.court ?? b.assignment?.court ?? "")));
    return <>{header}<main className={"workspace" + (viewMode === "board" ? " board-workspace" : "")}><section className="heading"><div><p className="eyebrow">SCHEDULE OPERATIONS</p><h1>{state.name}</h1><p>點選比賽，再點綠色空檔；多場調整最後一起確認儲存。</p></div><button disabled={busy || draftCount > 0} onClick={() => run(load)}>重新整理</button></section>{error}
    <LiveControl revision={data!.revision} draftCount={draftCount} api={api} onChanged={load}/>
    <div className="policy"><span>08:00–17:30 開賽</span><span>賽後休息 ≥60 分鐘</span><span>每人每日 ≤5 場</span><span>只檢查已確定選手</span></div>
    {data!.issues.length > 0 && <div className="notice error">目前賽程有 {data!.issues.length} 項衝突，請先修正後再儲存。{data!.issues.map(i => <p key={i.key}>{i.message}</p>)}</div>}
    <section className="schedule-sheet"><div className="sheet-top"><div className="day-tabs" aria-label="比賽日期">{state.dates.map(d => <button key={d} className={day === d ? "selected" : ""} aria-pressed={day === d} onClick={() => setDay(d)}>{d.slice(5).replace("-", "/")}<small>{state.matches.filter(m => localTime((draft[m.id] ?? m.assignment)?.start ?? 0).startsWith(d)).length} 場</small></button>)}</div><button aria-pressed={showHistory} onClick={() => setShowHistory(!showHistory)}>異動紀錄</button></div>
    <button className="mobile-filter-toggle" aria-expanded={filtersOpen} aria-controls="schedule-filters" onClick={() => setFiltersOpen(!filtersOpen)}>搜尋與篩選{query || division ? " · 已套用" : ""}<span>{filtersOpen ? "收合 ↑" : "展開 ↓"}</span></button>
    <div id="schedule-filters" className={"filters" + (filtersOpen ? " is-open" : "")}><MatchSearch state={state} draft={draft} query={query} onQuery={setQuery} onLocate={locateMatch}/><label>組別<select value={division} onChange={e => setDivision(e.target.value)}><option value="">全部組別</option>{divisions.map(d => <option key={d}>{d}</option>)}</select></label><label>場地<select value={court} onChange={e => setCourt(e.target.value)}><option value="">全部場地</option>{state.courts.map(c => <option value={c.id} key={c.id}>{c.name}</option>)}</select></label><p>{filtered.length} 場符合條件</p></div>
    {showHistory && <section className="history"><h2>最近 30 筆版本紀錄</h2>{data!.history.map(row => <details key={row.id}><summary>{new Date(row.created).toLocaleString("zh-TW")} · {row.reason.startsWith("Live ") ? "場地狀態更新" : `${row.changes.length} 場調整`} · {row.actor}</summary><p>{row.reason}</p>{row.changes.map(c => <p key={c.id}>第 {c.code} 場：{point(c.before)} → {point(c.after)}</p>)}<small>版本 {row.id.slice(0, 8)}</small></details>)}</section>}
    <div className="view-toolbar"><div className="view-switch" aria-label="賽程顯示方式"><button aria-pressed={viewMode === "board"} onClick={() => setViewMode("board")}>場地空檔表</button><button aria-pressed={viewMode === "list"} onClick={() => setViewMode("list")}>場次清單</button></div><span>{viewMode === "board" ? "篩選外場次仍顯示，場地占用不會被隱藏" : "逐場檢視與手動指定時間"}</span></div>
    {viewMode === "board" ? <ScheduleBoard state={state} draft={draft} day={day} court={court} matches={filtered} busy={busy} onMove={addToDraft} onEdit={openEditor} onDiscard={discardMatch} onCourtChange={setCourt} focusRequest={focusRequest} onBatchMove={addBatchToDraft} onLocate={locateMatch}/> : <>
    <div className="list-head"><span>開賽／場地</span><span>對戰與組別</span><span>操作</span></div><div className="match-list">{filtered.map(m => { const a = draft[m.id] ?? m.assignment, changed = !!draft[m.id], locked = ["completed", "in_progress"].includes(m.status) || m.lock_time && m.lock_court; return <article className={"match-row" + (changed ? " changed" : "")} key={m.id}><div className="match-time"><strong>{a ? localTime(a.start).slice(11) : "未排程"}</strong><span>{a ? courtName(a.court) : "待安排"}</span></div><div className="match-info"><div className="match-meta"><span>第 {m.code} 場</span><span>{m.division} · {m.stage}</span>{changed && <b>草案</b>}</div><h2>{m.side_a}<span className="versus">對</span>{m.side_b}</h2>{changed && <p className="before">原：{point(m.assignment)}</p>}</div><button disabled={busy || locked} onClick={() => openEditor(m)}>{locked ? m.status === "completed" ? "已完成" : m.status === "in_progress" ? "比賽中" : "已鎖定" : changed ? "編輯草案" : "調整"}</button></article>; })}{!filtered.length && <div className="empty"><h2>沒有符合條件的場次</h2><p>試著清除搜尋、組別或場地篩選。</p><button onClick={() => { setQuery(""); setCourt(""); setDivision(""); }}>清除篩選</button></div>}</div></>}
    </section>
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
