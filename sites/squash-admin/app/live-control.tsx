"use client";
import { useCallback, useEffect, useRef, useState } from "react";
type Match = {
    id: string;
    code: string;
    division: string;
    side_a: string;
    side_b: string;
    planned_start: string | null;
    started_at?: string;
    eligible?: boolean;
};
type Control = {
    public_url: string | null;
    revision: string;
    sequence: number;
    published_revision: string | null;
    has_unpublished: boolean;
    pending: boolean;
    error: string | null;
    published_at: string | null;
    synced_at: string | null;
    courts: {
        id: string;
        name: string;
        current: Match | null;
        queue: Match[];
    }[];
};
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
export default function LiveControl({ revision, draftCount, api, onChanged }: {
    revision: string;
    draftCount: number;
    api: <T>(path: string, body?: unknown) => Promise<T>;
    onChanged: () => Promise<void>;
}) {
    const [data, setData] = useState<Control | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false), [selected, setSelected] = useState<Record<string, string>>({});
    const [review, setReview] = useState(false), [action, setAction] = useState<{
        court_id: string;
        match: Match;
        action: "start" | "end" | "cancel";
    } | null>(null), [reason, setReason] = useState("");
    const confirmationRef = useRef<HTMLDivElement>(null);
    useEffect(() => { if (review || action) {
        confirmationRef.current?.focus();
        confirmationRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    } }, [review, action]);
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
        setData(await api<Control>(path, body));
        setReview(false);
        setAction(null);
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
    return <section className="live-control" aria-labelledby="live-control-title"><div className="live-control-heading"><div><p className="eyebrow">COURT CONTROL</p><h2 id="live-control-title">現場場地與公開賽程</h2><p>賽程確認後發布；開賽與結束會同步到參賽者網站。</p></div>{data?.public_url && <a href={data.public_url} target="_blank" rel="noreferrer">查看參賽者頁面 ↗</a>}</div>
 {error && <p role="alert" className="notice error">{error}</p>}
 {data && <><div className="publication-status"><div><strong>{data.pending ? "已保存・等待同步" : data.has_unpublished ? "有尚未發布的賽程" : data.published_revision ? "公開網站已收到更新" : "尚未發布雲端賽程"}</strong><p>{data.error ?? (data.published_at ? `賽程發布 ${time(data.published_at)} · 最近同步 ${time(data.synced_at)}` : "先發布目前已儲存的賽程，再操作各場地。")}</p>{draftCount > 0 && <p>請先儲存或清空 {draftCount} 場草案。</p>}</div><div>{data.pending && <button disabled={busy} onClick={() => run("sync-retry", {})}>重試同步</button>}<button className="primary" disabled={busy || draftCount > 0 || !data.has_unpublished} onClick={() => setReview(true)}>發布已儲存賽程</button></div></div>
 <div className="live-court-grid">{data.courts.map(court => <article className={"live-court" + (court.current ? " is-playing" : "")} key={court.id}><div className="live-court-top"><h3>{court.name}</h3><span>{court.current ? "比賽中" : "目前未開賽"}</span></div>{court.current ? <><p className="live-division">{court.current.division} · 第 {court.current.code} 場</p><h4>{court.current.side_a}<span>對</span>{court.current.side_b}</h4><p>預定 {time(court.current.planned_start)}<br />實際開賽 {time(court.current.started_at)}</p><button className="primary" disabled={busy || draftCount > 0} onClick={() => setAction({ court_id: court.id, match: court.current!, action: "end" })}>結束這場比賽</button><button disabled={busy || draftCount > 0} onClick={() => setAction({ court_id: court.id, match: court.current!, action: "cancel" })}>撤銷誤按開賽</button></> : <><p>選擇現場準備上場的比賽，不會依時鐘自動開賽。</p><label>待開賽場次<select value={selected[court.id] ?? ""} disabled={busy || !data.published_revision} onChange={e => setSelected({ ...selected, [court.id]: e.target.value })}><option value="">選擇場次</option>{court.queue.map(match => <option key={match.id} value={match.id} disabled={!match.eligible}>{time(match.planned_start)} · {match.division} · {match.side_a} 對 {match.side_b}{!match.eligible ? "（需確認選手或先發布調整）" : ""}</option>)}</select></label><button className="primary" disabled={busy || draftCount > 0 || !court.queue.find(m => m.id === selected[court.id] && m.eligible)} onClick={() => setAction({ court_id: court.id, match: court.queue.find(m => m.id === selected[court.id])!, action: "start" })}>確認選手，開始比賽</button></>}</article>)}</div><p className="footnote">只顯示管理員確認的場上狀態。同步失敗會保留資料，操作台開啟時自動重試。結束比賽不代表已登錄成績。</p></>}
 {review && data && <div ref={confirmationRef} tabIndex={-1} className="live-confirm" role="region" aria-label="發布確認"><h3>發布目前已儲存的賽程？</h3><p>參賽者將看到這一版的時間與場地。尚未儲存的草案不會發布。</p><button disabled={busy} onClick={() => setReview(false)}>返回</button><button className="primary" disabled={busy || draftCount > 0} onClick={() => run("publish", { revision: data.revision, sequence: data.sequence })}>{busy ? "同步中…" : "確認發布"}</button></div>}
 {action && data && <div ref={confirmationRef} tabIndex={-1} className="live-confirm" role="region" aria-label="場地狀態確認"><h3>{action.action === "start" ? "確認雙方已上場" : action.action === "end" ? "確認比賽已結束" : "撤銷誤按開賽"}</h3><p>{action.match.division} · 第 {action.match.code} 場<br />{action.match.side_a} 對 {action.match.side_b}</p>{action.action === "cancel" && <label>撤銷原因<input maxLength={300} value={reason} onChange={e => setReason(e.target.value)}/></label>}<button disabled={busy} onClick={() => { setAction(null); setReason(""); }}>返回</button><button className="primary" disabled={busy || draftCount > 0 || action.action === "cancel" && !reason.trim()} onClick={() => run("live", { revision: data.revision, sequence: data.sequence, court_id: action.court_id, match_id: action.match.id, action: action.action, ...(action.action === "cancel" ? { reason } : {}) })}>{busy ? "同步中…" : "確認並同步"}</button></div>}
 </section>;
}
