"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Scoreboard from "./scoreboard";
import { preferredQueueDate, queueForDate, taipeiDate } from "../lib/court-queue";
import DirectResult from "./direct-result";
import type { ScoringRules } from "../lib/scoring";
import PublicationPreview from "./publication-preview";
import type { ReviewSnapshot } from "./publication-preview";
import type { PublicationReview } from "../lib/publication-diff";
type Match = {
    id: string;
    code: string;
    division: string;
    side_a: string;
    side_b: string;
    planned_start: string | null;
    planned_court?: string | null;
    started_at?: string;
    eligible?: boolean;
    blocked_reason?: string | null;
    scoring_rules?: ScoringRules;
};
type Control = {
    public_url: string | null;
    dates: string[];
    publication_review: PublicationReview;
    revision: string;
    sequence: number;
    published_revision: string | null;
    has_unpublished: boolean;
    pending: boolean;
    error: string | null;
    published_at: string | null;
    synced_at: string | null;
    queue: Match[];
    courts: {
        id: string;
        name: string;
        current: Match | null;
        queue: Match[];
    }[];
};
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
export default function LiveControl({ mode, revision, draftCount, api, onChanged }: {
    mode: "live" | "publication";
    revision: string;
    draftCount: number;
    api: <T>(path: string, body?: unknown) => Promise<T>;
    onChanged: () => Promise<void>;
}) {
    const [data, setData] = useState<Control | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false), [selected, setSelected] = useState("");
    const [day, setDay] = useState(""), [query, setQuery] = useState(""), [startCourt, setStartCourt] = useState("");
    const [result, setResult] = useState<{ match: Match; revision: string; sequence: number; scoreVersion?: number } | null>(null);
    useEffect(() => { if (data) setDay(previous => data.dates.includes(previous) ? previous : preferredQueueDate(data.dates)); }, [data]);
    const [review, setReview] = useState<ReviewSnapshot | null>(null), [action, setAction] = useState<{
        court_id: string;
        match: Match;
        action: "start" | "end" | "cancel";
    } | null>(null), [reason, setReason] = useState("");
    const [scoring, setScoring] = useState<{court_id:string; court_name:string; match:Match}|null>(null);
    const confirmationRef = useRef<HTMLDivElement>(null);
    useEffect(() => { if (action) {
        confirmationRef.current?.focus();
        confirmationRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    } }, [action]);
    const refresh = useCallback(async () => setData(await api<Control>("control")), [api]);
    useEffect(() => { let active = true; const tick = async () => { try {
        const next = await api<Control>("control");
        if (!active)
            return;
        if (next.pending) {
            const retried = await api<Control>("sync-retry", {});
            if (active)
                setData(retried);
        }
        else
            setData(next);
    }
    catch (e) {
        if (active)
            setError((e as Error).message);
    } }; void tick(); const timer = setInterval(tick, 15000); return () => { active = false; clearInterval(timer); }; }, [api, revision]);
    async function run(path: string, body: unknown) { setBusy(true); setError(""); try {
        const next = await api<Control>(path, body);
        setData(next);
        if (path === "live" && action?.action === "start") setScoring({court_id:action.court_id,court_name:next.courts.find(c=>c.id===action.court_id)?.name??"",match:action.match});
        setReview(null);
        setAction(null);
        setResult(null);
        setReason("");
        await onChanged();
    }
    catch (e) {
        setError((e as Error).message);
        await refresh().catch(() => { });
    }
    finally {
        setBusy(false);
    } }
    async function openResult(match: Match, live: boolean) {
        if (!data || busy) return;
        setError("");
        setAction(null);
        setBusy(true);
        try {
            const score = live ? await api<{ version: number }>(`score?match_id=${encodeURIComponent(match.id)}`) : null;
            setResult({ match, revision: data.revision, sequence: data.sequence, scoreVersion: score?.version });
        } catch (error) {
            setError(error instanceof Error ? error.message : "無法讀取比分。");
        } finally {
            setBusy(false);
        }
    }
    const term = query.trim().toLocaleLowerCase();
    const number = /^(?:第\s*)?(\d+)(?:\s*場)?$/.exec(term)?.[1];
    const queue = term ? (data?.queue ?? []).filter(match => number ? match.code === number
        : [match.code, match.side_a, match.side_b, match.division].some(value => value.toLocaleLowerCase().includes(term)))
        : queueForDate(data?.queue ?? [], day);
    const picked = queue.find(match => match.id === selected) ?? (queue.length === 1 ? queue[0] : null);
    const available = data?.courts.filter(court => !court.current) ?? [];
    const selectedCourt = available.find(court => court.id === startCourt);
    const reviewStale = !!review && (data?.revision !== review.revision || data?.sequence !== review.sequence);
    return <section id={mode === "publication" ? "schedule-publication" : undefined} className="live-control" aria-labelledby="live-control-title"><div className="live-control-heading"><div><h2 id="live-control-title">{mode === "live" ? "找場次開賽" : "發布給參賽者"}</h2><p>{mode === "live" ? "先找場次，再選空場；補登完賽不用選場地。" : "調整並儲存後，在這裡預覽變更並發布。"}</p></div>{data?.public_url && <a href={data.public_url} target="_blank" rel="noreferrer">查看參賽者頁面 ↗</a>}</div>
 {error && <p role="alert" className="notice error">{error}</p>}
 {data && <>{mode === "publication" ? <div className="publication-status"><div><strong>{data.pending ? "已保存・等待同步" : !data.published_revision ? "首次使用：請先發布賽程以啟用計分" : data.has_unpublished ? "賽程已儲存，選手還看不到這次修改" : "選手頁面已更新"}</strong><p>{data.error ?? (data.published_at ? `賽程發布 ${time(data.published_at)} · 最近同步 ${time(data.synced_at)}` : "可以先選擇場次；開賽前會引導你確認並發布賽程。")}</p>{draftCount > 0 && <p>請先儲存或清空 {draftCount} 場草案。</p>}</div><div>{data.pending && <button disabled={busy} onClick={() => run("sync-retry", {})}>重試同步</button>}<button className="primary" disabled={busy || draftCount > 0 || !data.has_unpublished} onClick={() => {setError("");setAction(null);setReview({...data.publication_review,revision:data.revision,sequence:data.sequence});}}>{!data.has_unpublished ? (data.pending ? "等待同步" : "目前無新變更") : data.publication_review.initial ? "預覽整份賽程 →" : `查看 ${data.publication_review.changes.length} 場變更 →`}</button></div></div> : <div className="live-sync-status" role="status"><strong>{data.pending ? "已保存・等待同步" : "比分與完賽成績自動同步"}</strong>{data.pending && <button disabled={busy} onClick={() => run("sync-retry", {})}>重試同步</button>}{data.has_unpublished && <p>有尚未發布的賽程，請到<a href="/schedule">賽程調整</a>確認並發布。</p>}{data.error && <p>{data.error}</p>}</div>}
 {mode === "live" && <>
    <section className="live-confirm" aria-label="待賽場次">
        <h3>待賽場次</h3>
        <label>搜尋場號或選手<input type="search" value={query} disabled={busy} placeholder="例如 42" onChange={e => {setQuery(e.target.value);setSelected("");setStartCourt("");setAction(null);setResult(null);}}/></label>
        {!term && <nav className="queue-dates" aria-label="待賽日期">{data.dates.map(date => <button key={date} type="button" disabled={busy} aria-pressed={day === date} onClick={() => {setDay(date);setSelected("");setStartCourt("");setAction(null);setResult(null);}}>{date === taipeiDate() ? "今天 · " : ""}{date.slice(5).replace("-", "/")}</button>)}</nav>}
        <label>選擇場次<select value={picked?.id ?? ""} disabled={busy || !queue.length} onChange={e => {setSelected(e.target.value);setStartCourt("");setAction(null);setResult(null);}}>
            <option value="">選擇場次</option>
            {queue.map(match => <option key={match.id} value={match.id} disabled={!match.eligible}>第 {match.code} 場 · {time(match.planned_start)} · {match.side_a} 對 {match.side_b}{match.blocked_reason ? `（${match.blocked_reason}）` : ""}</option>)}
        </select></label>
        {!queue.length && <p>沒有符合條件的待賽場次。</p>}
        {picked && <p>第 {picked.code} 場 · {picked.division}<br/><strong>{picked.side_a} 對 {picked.side_b}</strong>{picked.planned_court && <><br/>預排參考：{picked.planned_court}</>}</p>}
        {picked?.eligible && <label>實際開賽場地<select value={selectedCourt?.id ?? ""} disabled={busy || !available.length} onChange={e => {setStartCourt(e.target.value);setAction(null);}}>
            <option value="">{available.length ? "選擇空場" : "目前沒有空場"}</option>
            {available.map(court => <option key={court.id} value={court.id}>{court.name}</option>)}
        </select></label>}
        <button className="primary" disabled={busy || draftCount > 0 || !data.published_revision || !picked?.eligible || !selectedCourt}
            onClick={() => {if (picked && selectedCourt) {setResult(null);setAction({court_id: selectedCourt.id, match: picked, action: "start"});}}}>確認開賽</button>
        <button disabled={busy || draftCount > 0 || !data.published_revision || !picked?.eligible} onClick={() => {if (picked) void openResult(picked, false);}}>補登完賽</button>
    </section>
    <h3>場地看板</h3>
    <div className="live-court-grid">{data.courts.map(court => {
        const current = court.current;
        return <article className={"live-court" + (current ? " is-playing" : "")} key={court.id}>
            <div className="live-court-top"><h4>{court.name}</h4><span>{current ? "比賽中" : "空場"}</span></div>
            {current && <><p className="live-division">{current.division} · 第 {current.code} 場</p>
                <p><strong>{current.side_a} 對 {current.side_b}</strong><br/>實際開賽 {time(current.started_at)}</p>
                <button className="primary" disabled={busy} onClick={() => {setResult(null);setScoring({court_id: court.id, court_name: court.name, match: current});}}>進入計分模式</button>
                <button disabled={busy} onClick={() => void openResult(current, true)}>補登完賽</button>
                <button disabled={busy} onClick={() => {setResult(null);setAction({court_id: court.id, match: current, action: "cancel"});}}>撤銷誤按開賽</button>
            </>}
        </article>;
    })}</div>
    {result && <DirectResult key={result.match.id} match={result.match} busy={busy} onClose={() => setResult(null)}
        onSubmit={games => void run("live", {revision: result.revision, sequence: result.sequence, match_id: result.match.id, action: "record-result", games, ...(result.scoreVersion === undefined ? {} : {score_version: result.scoreVersion})})}/>}
    <p className="footnote">現場開賽不會修改預排場地。每次得分都會儲存，完賽確認後自動同步成績。</p>
 </>}</>}
 {review && data && <PublicationPreview key={`${review.revision}:${review.sequence}`} review={review} stale={reviewStale} busy={busy} draftCount={draftCount} error={error} onClose={()=>setReview(null)} onReload={()=>{setError("");setReview({...data.publication_review,revision:data.revision,sequence:data.sequence});}} onPublish={()=>run("publish",{revision:review.revision,sequence:review.sequence})}/>}

 {action && data && <div ref={confirmationRef} tabIndex={-1} className="live-confirm" role="region" aria-label="場地狀態確認"><h3>{action.action === "start" ? "確認雙方已上場" : action.action === "end" ? "確認比賽已結束" : "撤銷誤按開賽"}</h3><p>{action.match.division} · 第 {action.match.code} 場<br />{action.match.side_a} 對 {action.match.side_b}</p>{action.action === "start" && <p>開賽場地：{data.courts.find(c=>c.id===action.court_id)?.name}<br />{(action.match.scoring_rules?.best_of??3)===3?"三局兩勝":"五局三勝"} · 每局 {action.match.scoring_rules?.target??11} 分 · 領先 2 分勝出</p>}{action.action === "cancel" && <label>撤銷原因<input maxLength={300} value={reason} onChange={e => setReason(e.target.value)}/></label>}<button disabled={busy} onClick={() => { setAction(null); setReason(""); }}>返回</button><button className="primary" disabled={busy || draftCount > 0 || action.action === "cancel" && !reason.trim() || action.action === "start" && data.courts.some(c => c.id === action.court_id && c.current)} onClick={() => {
     void run("live", { revision: data.revision, sequence: data.sequence, court_id: action.court_id, match_id: action.match.id, action: action.action, ...(action.action === "cancel" ? { reason } : {}) });
 }}>{busy ? "同步中…" : "確認並同步"}</button></div>}
 {scoring && <Scoreboard key={scoring.match.id} match={scoring.match} court={scoring.court_name} api={api} onClose={()=>setScoring(null)} onFinish={async(version)=>{
     const latest=await api<Control>("control");
     const next=await api<Control>("live",{revision:latest.revision,sequence:latest.sequence,court_id:scoring.court_id,match_id:scoring.match.id,action:"end",score_version:version});
     setData(next);setScoring(null);await onChanged();
 }}/>}
 </section>;
}
