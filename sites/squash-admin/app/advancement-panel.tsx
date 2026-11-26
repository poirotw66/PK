"use client";
import { useState } from "react";
import type { Tournament } from "../lib/schedule/schema";
import type { AdvancementChange } from "../lib/advancement-model";

type Plan = { id: string | null; can_apply: boolean; decision_changed: boolean; source_attached: boolean; changes: AdvancementChange[]; pending: { id: string; code: string; message: string }[]; blockers: { id: string; code: string; message: string }[]; issues: { key: string; message: string }[] };
export default function AdvancementPanel({ state, revision, api, onChanged }: {
    state: Tournament; revision: string; api: <T>(path: string, body?: unknown) => Promise<T>; onChanged: () => Promise<void>;
}) {
    const [reason, setReason] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
    const [plan, setPlan] = useState<Plan | null>(null), [basis, setBasis] = useState("");
    const [source, setSource] = useState<Tournament | null>(null);
    const [orderings, setOrderings] = useState<Record<string, string[]>>({});
    const stale = !!plan && basis !== revision;
    const entries = new Map(((source ?? state).entries ?? []).map(entry => [entry.id, state.players.find(player => player.id === entry.player_id)?.name ?? entry.id]));
    const pools = ((source ?? state).advancement_pools ?? []).filter(pool => (source ?? state).matches.some(match => match.slots && Object.values(match.slots).some(source => source.kind === "pool_rank" && source.pool_id === pool.id)));
    const hasSources = (source ?? state).matches.some(match => match.slots && Object.values(match.slots).some(source => ["winner", "loser", "pool_rank"].includes(source.kind)));
    async function preview() {
        setBusy(true); setError(""); setMessage(""); setPlan(null);
        try {
            const control = await api<{ revision: string; sequence: number }>("control");
            if (control.revision !== revision) throw new Error("賽程或成績已更新，請先重新整理。");
            const pool_orderings = pools.filter(pool => orderings[pool.id]?.some(Boolean)).map(pool => ({ stage_id: pool.stage_id, pool_id: pool.id, entry_ids: orderings[pool.id] }));
            setPlan(await api<Plan>("advancement-preview", { revision, sequence: control.sequence, reason: reason.trim(), pool_orderings, ...(source ? { source_state: source } : {}) })); setBasis(revision);
        } catch (error) { setError(error instanceof Error ? error.message : "無法核對晉級。"); }
        finally { setBusy(false); }
    }
    return <details className="experimental-scoring"><summary>核對晉級與小組名次</summary><p>依已確認成績核對下一輪選手，預覽後確認套用，再到賽程安排發布。小組名次及同分裁定須由賽務明確核定。</p>
        {!state.matches.some(match => match.slots) && <><p>既有交接檔沒有晉級來源。可選擇同一原始地端版本的新格式 JSON，先預覽補入來源，再核對晉級；原有賽程與成績不會被檔案覆蓋。</p><label>補入原賽事的晉級來源<input type="file" accept=".json,application/json" disabled={busy || !!plan} onChange={async event => { const file = event.target.files?.[0]; setError(""); setSource(null); setOrderings({}); if (!file) return; try { if (file.size > 1024 * 1024) throw new Error("交接檔不得超過1 MB。"); const payload = JSON.parse(await file.text()); setSource(payload.state ?? payload); } catch (error) { setError(error instanceof Error ? error.message : "交接檔無效。"); } }}/></label></>}
        {error && <p role="alert" className="notice error">{error}</p>}{message && <p role="status" className="notice success">{message}</p>}
        {!plan && hasSources && <><label>核對原因<textarea value={reason} maxLength={500} rows={2} disabled={busy} onChange={event => setReason(event.target.value)}/></label>
            {pools.map(pool => <details key={pool.id}><summary>人工核定：{pool.label}</summary><p>僅在該小組所有成績已確認後填寫，每位選手只能出現一次。未核定時保留待確認。</p>{pool.entry_ids.map((_, index) => <label key={index}>第 {index + 1} 名<select disabled={busy} value={orderings[pool.id]?.[index] ?? ""} onChange={event => setOrderings(previous => { const values = [...(previous[pool.id] ?? pool.entry_ids.map(() => ""))]; values[index] = event.target.value; return { ...previous, [pool.id]: values }; })}><option value="">待核定</option>{pool.entry_ids.map(id => <option key={id} value={id}>{entries.get(id)}</option>)}</select></label>)}</details>)}
            <button disabled={busy || !reason.trim()} onClick={() => void preview()}>{busy ? "核對中…" : "預覽晉級變更"}</button></>}
        {plan && <><h3>晉級核對結果</h3>{plan.changes.map(change => <article key={change.id}><strong>第 {change.code} 場</strong><p>原對戰：{change.before.side_a} 對 {change.before.side_b}</p><p>核對後：{change.after.side_a} 對 {change.after.side_b}</p></article>)}
            {plan.source_attached && <p>確認後會補入核對過的晉級來源，保留現有成績與時間場地。</p>}
            {!plan.changes.length && <p>沒有需要更換選手的場次。{plan.decision_changed ? "確認後會保存人工核定名次。" : ""}</p>}
            {plan.pending.map(item => <p key={item.id}>第 {item.code} 場：{item.message}</p>)}
            {plan.blockers.map(item => <p className="notice error" key={item.id}>第 {item.code} 場：{item.message}</p>)}{plan.issues.map(issue => <p className="notice error" key={issue.key}>{issue.message}</p>)}
            {stale && <p role="alert">成績或賽程已更新，請返回重新核對。</p>}
            <div className="dialog-actions"><button disabled={busy} onClick={() => setPlan(null)}>返回核對</button><button className="primary" disabled={busy || stale || !plan.can_apply || !plan.id} onClick={async () => { setBusy(true); setError(""); try { await api("advancement-confirm", { id: plan.id }); setPlan(null); setReason(""); setMessage("晉級已確認，請到賽程安排預覽並發布。"); try { await onChanged(); } catch { setMessage("晉級已儲存。暫時無法取得最新資料，請重新整理；不必再次套用。"); } } catch (error) { setError(error instanceof Error ? error.message : "無法儲存晉級。"); } finally { setBusy(false); } }}>{busy ? "儲存中…" : "確認套用晉級"}</button></div>
        </>}
    </details>;
}
