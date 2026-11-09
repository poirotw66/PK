"use client";
import { useEffect, useRef } from "react";
import type { PublicationChange, PublicationReview, PublishedPosition } from "../lib/publication-diff";
export type ReviewSnapshot = PublicationReview & { revision: string; sequence: number };
const date = (value: string) => new Date(value).toLocaleDateString("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit" });
const clock = (value: string) => new Date(value).toLocaleTimeString("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", hour12: false });
function position(value: PublishedPosition) { return value ? `${date(value.start)} ${clock(value.start)} · ${value.court_name}` : "時間與場地待安排"; }
function description(change: PublicationChange) {
    if (change.kind === "initial") return "首次發布";
    if (change.kind === "added") return "新增比賽";
    if (change.kind === "removed") return "從公開賽程移除此場";
    const labels = [];
    if (change.before?.start !== change.after?.start) {
        if (!change.before || !change.after) labels.push(change.after ? "已安排時間" : "時間改為待安排");
        else if (date(change.before.start) !== date(change.after.start)) labels.push("比賽日期已調整");
        else { const minutes = (Date.parse(change.after.start) - Date.parse(change.before.start)) / 60000; labels.push(`${minutes > 0 ? "延後" : "提早"} ${Math.abs(minutes)} 分鐘`); }
    }
    if (change.before?.court_id !== change.after?.court_id) labels.push("更換場地");
    return labels.join(" · ");
}
export default function PublicationPreview({ review, stale, busy, draftCount, error, onClose, onReload, onPublish }: {
    review: ReviewSnapshot; stale: boolean; busy: boolean; draftCount: number; error: string;
    onClose: () => void; onReload: () => void; onPublish: () => void;
}) {
    const dialog = useRef<HTMLDialogElement>(null);
    useEffect(() => { dialog.current?.showModal(); }, []);
    return <dialog ref={dialog} className="publication-preview" aria-labelledby="publication-preview-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
      <div className="publication-preview-heading"><div><p className="eyebrow">確認變更 → 發布給選手</p><h2 id="publication-preview-title">{review.initial ? "首次發布賽程" : `確認 ${review.changes.length} 場變更`}</h2></div><button disabled={busy} aria-label="關閉發布預覽" onClick={onClose}>×</button></div>
      <p className="publication-preview-intro">{review.initial ? `共 ${review.total} 場，發布後選手就能查看。` : "請確認新時間與場地，再發布。"}</p>
      {error && !stale && <p className="notice error" role="alert">{error}</p>}
      {stale && <p className="notice error" role="alert">賽程剛剛有更新，請重新載入變更再確認。</p>}
      {draftCount > 0 && <p className="notice error" role="alert">另有 {draftCount} 場草案尚未儲存，請先返回處理。</p>}
      <div className="publication-preview-list">{review.changes.map(change => <article key={change.id} className="publication-preview-match">
        <p className="publication-preview-meta">第 {change.code} 場 · {change.division}</p><h3>{change.side_a}<span>對</span>{change.side_b}</h3><p className="publication-preview-change">{description(change)}</p>
        {change.after && change.kind !== "removed" ? <div className="publication-preview-destination"><div><span>新時間 · {date(change.after.start)}</span><strong>{clock(change.after.start)}</strong></div><div><span>新場地</span><strong>{change.after.court_name}</strong></div></div> : <p className="publication-preview-unassigned">{change.kind === "removed" ? "發布後不再顯示此場比賽" : "時間與場地待安排"}</p>}
        {change.kind !== "initial" && change.kind !== "added" && <p className="publication-preview-before">原訂：{position(change.before)}</p>}
      </article>)}</div>
      {!review.changes.length && <p>這次沒有時間、場地或場次增減。</p>}
      <div className="publication-preview-actions"><button disabled={busy} onClick={onClose}>返回修改</button>{stale ? <button className="primary" disabled={busy} onClick={onReload}>重新載入變更</button> : <button className="primary" disabled={busy || draftCount > 0} onClick={onPublish}>{busy ? "正在發布…" : "發布給參賽者"}</button>}</div>
    </dialog>;
}
