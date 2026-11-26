"use client";

import { useEffect, useRef, useState } from "react";
import type { Match, Tournament, Update } from "../lib/schedule/schema";
import { localTime } from "../lib/schedule/rules";

type Change = { id: string; code: string; before: Match["assignment"]; after: Update };
type HandoffReview = { id: string; can_apply: boolean; changes: Change[]; policy_changes?: {id:string;code:string;before:string[];after:string[]}[]; introduced: { key: string; message: string }[]; remaining: { key: string; message: string }[] };
type Props = {
    state: Tournament;
    api: <T = unknown>(path: string, body?: unknown) => Promise<T>;
    onClose: () => void;
    onChanged: () => Promise<void>;
    onSaved: () => void;
};

export default function LocalHandoff({ state, api, onClose, onChanged, onSaved }: Props) {
    const dialog = useRef<HTMLDialogElement>(null);
    const reviewHeading = useRef<HTMLHeadingElement>(null);
    const [direction, setDirection] = useState<"download" | "upload">("download");
    const [file, setFile] = useState<File | null>(null), [reason, setReason] = useState("");
    const [review, setReview] = useState<{ plan: HandoffReview; reason: string } | null>(null);
    const [busy, setBusy] = useState(false), [message, setMessage] = useState(""), [error, setError] = useState("");
    useEffect(() => {
        const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const element = dialog.current;
        element?.showModal();
        return () => {
            element?.close();
            if (trigger?.isConnected) trigger.focus();
        };
    }, []);
    useEffect(() => { if (review) reviewHeading.current?.focus(); }, [review]);
    async function run(task: () => Promise<void>) {
        setBusy(true); setError(""); setMessage("");
        try { await task(); }
        catch (error) { setError(error instanceof Error ? error.message : "目前無法完成交接，請稍後重試。"); }
        finally { setBusy(false); }
    }
    function changeDirection(next: "download" | "upload") {
        if (next === direction) return;
        setDirection(next); setReview(null); setFile(null); setError(""); setMessage("");
    }
    async function download() {
        const payload = await api("local-return");
        const url = URL.createObjectURL(new Blob([JSON.stringify(payload)], { type: "application/json" }));
        const link = document.createElement("a");
        link.href = url; link.download = "pk-cloud-return.json"; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setMessage("交接檔已開始下載。請回地端操作台核對後匯入；檔案保留下載當時的版本。");
    }
    async function preview() {
        if (!file || !reason.trim()) return;
        if (file.size > 1024 * 1024) throw new Error("檔案不得超過 1 MB。");
        let envelope: unknown;
        try { envelope = JSON.parse(await file.text()); }
        catch { throw new Error("交接檔不是有效的 JSON，請重新選擇地端匯出的重排檔。"); }
        const snapshot = reason.trim();
        const plan = await api<HandoffReview>("local-update-preview", { envelope, reason: snapshot });
        setReview({ plan, reason: snapshot });
    }
    const outcomeNames: Record<string,string> = {played:"正常完賽",walkover:"未賽棄權",retired:"傷退",double_forfeit:"雙方棄權"};
    const position = (value: Match["assignment"]) => value
        ? `${localTime(value.start).replace("T", " ")} · ${state.courts.find(court => court.id === value.court)?.name ?? value.court}`
        : "未排程";
    return <dialog ref={dialog} className="handoff-dialog" aria-labelledby="handoff-title" aria-describedby="handoff-description"
        onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
        <div className="sheet-dialog-heading"><div><p className="eyebrow">賽務工具</p><h2 id="handoff-title">地端與雲端交接</h2></div><button type="button" disabled={busy} aria-label="關閉地端交接" onClick={onClose}>×</button></div>
        <p id="handoff-description" className="dialog-description">下載最新雲端資料，或核對地端重排後的賽程。</p>
        {!review && <div className="handoff-directions" role="group" aria-label="交接方向">
            <button type="button" disabled={busy} aria-pressed={direction === "download"} onClick={() => changeDirection("download")}>取回雲端資料</button>
            <button type="button" disabled={busy} aria-pressed={direction === "upload"} onClick={() => changeDirection("upload")}>匯入地端重排</button>
        </div>}
        {error && <p className="notice error" role="alert">{error}</p>}
        {message && <p className="notice success" role="status">{message}</p>}
        {review ? <>
            <h3 ref={reviewHeading} tabIndex={-1}>核對 {new Set([...review.plan.changes.map(change => change.id), ...(review.plan.policy_changes ?? []).map(change => change.id)]).size} 場交接變更</h3><p className="handoff-reason">交接原因：{review.reason}</p>
            {[...review.plan.introduced, ...review.plan.remaining].map(issue => <p className="conflict" key={issue.key}>{issue.message}</p>)}
            <div className="review-changes">{review.plan.policy_changes?.map(change => <article key={"policy-"+change.id}><strong>第 {change.code} 場 · 特殊結果規則核定</strong><p>原可登錄：{change.before.map(value => outcomeNames[value] ?? value).join("、")}</p><p>調整後：{change.after.map(value => outcomeNames[value] ?? value).join("、")}</p><p>此處僅更新地端已核定的結果類型；積分與條文仍以地端核定為準。</p></article>)}{review.plan.changes.map(change => <article key={change.id}><strong>第 {change.code} 場</strong><p>原訂：{position(change.before)}</p><p className="proposed">調整：{position(change.after)}</p></article>)}</div>
            {!review.plan.changes.length && <p className="handoff-note">時間與場地沒有變更；請一併核對上方規則核定變更，確認後會記錄新的地端來源版本。</p>}
            <p className="handoff-note">儲存後保留既有成績，仍需另行確認發布給選手。</p>
            <div className="dialog-actions"><button type="button" disabled={busy} onClick={() => { setReview(null); setFile(null); setError(""); }}>返回選檔</button><button type="button" className="primary" disabled={busy || !review.plan.can_apply || !review.plan.id} onClick={() => run(async () => {
                await api("local-update-confirm", { id: review.plan.id });
                setReview(null); setFile(null); setReason(""); onSaved();
                try { await onChanged(); onClose(); }
                catch { setMessage("地端重排已儲存。暫時無法讀取最新賽程，請返回賽程並重新整理；不必再次匯入。"); }
            })}>{busy ? "正在儲存…" : "確認儲存交接"}</button></div>
        </> : direction === "download" ? <section className="handoff-content" aria-labelledby="handoff-download-title">
            <h3 id="handoff-download-title">把現場資料帶回地端</h3>
            <p>檔案包含已儲存賽程、確認成績及實際開賽紀錄，可供地端重排與產生文件。</p>
            <ol><li>先結束進行中的比賽，並確認完賽成績。</li><li>下載後，到地端「建賽與雲端交換」核對匯入。</li></ol>
            <p className="handoff-note">檔案含私人名單與成績，請保存在賽務電腦。</p>
            <div className="dialog-actions"><button type="button" disabled={busy} onClick={onClose}>返回賽程</button><button type="button" className="primary" disabled={busy} onClick={() => run(download)}>{busy ? "正在準備…" : "下載地端交接檔"}</button></div>
        </section> : <form className="handoff-content" onSubmit={event => { event.preventDefault(); void run(preview); }}>
            <h3>核對地端重排</h3><p>核對未開賽場次的時間與場地，以及地端已核定的特殊結果類型。既有比分與選手不會直接改寫；版本過期時會停止匯入。</p>
            <label htmlFor="handoff-file">地端重排交接檔<input id="handoff-file" type="file" accept=".json,application/json" required disabled={busy} onChange={event => { setFile(event.target.files?.[0] ?? null); setError(""); }}/></label>
            <label htmlFor="handoff-reason">交接原因<textarea id="handoff-reason" required maxLength={500} rows={3} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} placeholder="例如：核對現場成績後調整後續場次"/></label>
            <div className="dialog-actions"><button type="button" disabled={busy} onClick={onClose}>返回賽程</button><button className="primary" disabled={busy || !file || !reason.trim()}>{busy ? "正在檢查…" : "檢查地端重排"}</button></div>
        </form>}
    </dialog>;
}
