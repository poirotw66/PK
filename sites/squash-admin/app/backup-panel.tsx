"use client";
import { useRef, useState } from "react";

type BackupReview = {
    id: string; can_apply: boolean; initialization: boolean; completed: number; matches: number;
    exported_at: string; source_revision: string; metadata_changed: boolean;
    changes: { id: string; code: string; before: Record<string, unknown> | null; after: Record<string, unknown> }[];
};
const fieldNames: Record<string, string> = { status: "場次狀態", side_a: "A 方", side_b: "B 方", assignment: "時間與場地", result: "確認成績", players: "選手身分", scoring_rules: "計分規則", play: "比賽時間", buffer: "緩衝", rest: "休息", transfer: "轉場", dependencies: "前置場次", ready_after: "最早開賽", lock_time: "鎖定時間", lock_court: "鎖定場地" };
const describe = (value: unknown) => value === null || value === undefined ? "無" : typeof value === "string" ? value : JSON.stringify(value);
export default function BackupPanel({ api, onChanged, disabled = false }: {
    api: <T>(path: string, body?: unknown) => Promise<T>;
    onChanged: () => Promise<void>;
    disabled?: boolean;
}) {
    const [file, setFile] = useState<File | null>(null), [reason, setReason] = useState("");
    const [review, setReview] = useState<{ plan: BackupReview; reason: string } | null>(null);
    const [acknowledged, setAcknowledged] = useState(false), [busy, setBusy] = useState(false);
    const [error, setError] = useState(""), [message, setMessage] = useState("");
    const fileInput = useRef<HTMLInputElement>(null);
    async function run(task: () => Promise<void>) {
        setBusy(true); setError(""); setMessage("");
        try { await task(); }
        catch (error) { setError(error instanceof Error ? error.message : "目前無法處理備份，請稍後再試。"); }
        finally { setBusy(false); }
    }
    function reset() {
        setReview(null); setFile(null); setAcknowledged(false);
        if (fileInput.current) fileInput.current.value = "";
    }
    return <details className="experimental-scoring backup-panel"><summary>賽事資料備份與還原（主管理員）</summary>
        {disabled && <p role="status">請先儲存或清空賽程草案，再使用備份與還原。</p>}
        <p>備份包含賽程、已確認成績、實際開賽紀錄及操作歷程摘要。登入帳號、工作階段與網站秘密不會匯出或還原。</p>
        <p>請定期下載並另存到賽務電腦。這是手動應用資料備份，不取代平台資料庫備份；進行中比賽的備份僅供留存，不能直接還原。</p>
        <button type="button" disabled={disabled || busy || !!review} onClick={() => void run(async () => {
            const backup = await api("backup");
            const url = URL.createObjectURL(new Blob([JSON.stringify(backup)], { type: "application/json" }));
            const link = document.createElement("a"); link.href = url; link.download = `pk-admin-backup-${new Date().toISOString().slice(0, 10)}.json`; link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000); setMessage("備份檔已開始下載，請確認檔案已保存在賽務電腦。");
        })}>{busy ? "處理中…" : "下載賽事備份"}</button>
        {error && <p className="notice error" role="alert">{error}</p>}
        {message && <p className="notice success" role="status">{message}</p>}
        {!review ? <form className="live-confirm" onSubmit={event => { event.preventDefault(); void run(async () => {
            if (!file || !reason.trim()) return;
            if (file.size > 8 * 1024 * 1024) throw new Error("備份檔不得超過 8 MB。");
            let envelope: unknown;
            try { envelope = JSON.parse(await file.text()); } catch { throw new Error("備份不是有效的 JSON 檔案。"); }
            const capturedReason = reason.trim();
            const plan = await api<BackupReview>("backup-preview", { envelope, reason: capturedReason });
            setReview({ plan, reason: capturedReason }); setAcknowledged(false);
        }); }}>
            <h3>檢查要還原的備份</h3>
            <p>只接受原網站的備份。還原會取代目前賽程與成績，保留現站操作歷程並新增還原紀錄，再以新的發布序號更新選手頁面。</p>
            <p>檢查碼只驗證檔案完整性，不是網站簽章。請使用主管理員自行保管的備份。</p>
            <label>備份 JSON<input ref={fileInput} type="file" accept=".json,application/json" required disabled={disabled || busy} onChange={event => setFile(event.target.files?.[0] ?? null)}/></label>
            <label>還原原因<textarea required rows={3} maxLength={500} disabled={disabled || busy} value={reason} onChange={event => setReason(event.target.value)}/></label>
            <button className="primary" disabled={disabled || busy || !file || !reason.trim()}>{busy ? "檢查中…" : "預覽還原差異"}</button>
        </form> : <section className="live-confirm" aria-label="備份還原預覽">
            <h3>核對備份還原</h3>
            <p>備份時間：{new Date(review.plan.exported_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}（台北時間）</p>
            <p>{review.plan.matches} 場比賽、{review.plan.completed} 場已完賽；{review.plan.changes.length} 場有變更。</p>
            <p>還原原因：{review.reason}</p>
            {review.plan.initialization && <p>目前沒有賽程，確認後將從此備份建立賽事資料。</p>}
            {review.plan.metadata_changed && <p>賽事設定或來源版本亦將回復至備份內容。</p>}
            <div>{review.plan.changes.map(change => <details key={change.id}><summary>第 {change.code} 場的變更</summary>
                <dl>{Object.keys(change.after).filter(key => describe(change.before?.[key]) !== describe(change.after[key])).map(key => <div key={key}><dt>{fieldNames[key] ?? key}</dt><dd style={{ overflowWrap: "anywhere" }}>原：{describe(change.before?.[key])}<br/>還原：{describe(change.after[key])}</dd></div>)}</dl>
            </details>)}</div>
            <p>備份中的歷程摘要隨檔保留供查閱，不會冒用原操作者重建現站稽核紀錄。既有後台操作歷程與登入資料保留。</p>
            <label style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 44 }}><input style={{ width: 24, minHeight: 24, height: 24, flexShrink: 0 }} type="checkbox" checked={acknowledged} disabled={disabled || busy} onChange={event => setAcknowledged(event.target.checked)}/>我已確認差異，了解目前賽程與成績將被備份取代。</label>
            <button type="button" disabled={disabled || busy} onClick={reset}>返回選檔</button>
            <button type="button" className="primary" disabled={disabled || busy || !acknowledged || !review.plan.can_apply} onClick={() => void run(async () => {
                await api("backup-confirm", { id: review.plan.id });
                reset(); setReason(""); setMessage("備份已還原並建立新的發布，請核對選手頁面同步狀態。");
                try { await onChanged(); }
                catch { setMessage("備份已還原。暫時無法重新讀取資料，請重新整理；不必再次還原。"); }
            })}>{busy ? "還原中…" : "確認還原並發布"}</button>
        </section>}
    </details>;
}
