"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Match, Tournament, Update } from "../lib/schedule/schema";
import type { Neighbor } from "../lib/schedule/match-tools";
import { playerTimelines, reviewSwap } from "../lib/schedule/match-tools";
import { searchMatches } from "../lib/schedule/navigation";
import { editable } from "../lib/schedule/placements";
import { localTime } from "../lib/schedule/rules";
export type ToolMode = "swap" | "players";
type Props = {
    state: Tournament;
    selected: Match;
    mode: ToolMode;
    busy: boolean;
    onMode: (mode: ToolMode) => void;
    onClose: () => void;
    onSwap: (updates: Update[], focusId: string) => void;
    onLocate: (match: Match) => void;
};
export default function MatchTools({ state, selected, mode, busy, onMode, onClose, onSwap, onLocate }: Props) {
    const dialog = useRef<HTMLDialogElement>(null);
    const reviewPanel = useRef<HTMLElement>(null);
    const [query, setQuery] = useState(""), [limit, setLimit] = useState(20), [targetId, setTargetId] = useState(""), [error, setError] = useState("");
    useEffect(() => { if (dialog.current && !dialog.current.open)
        dialog.current.showModal(); }, []);
    useEffect(() => {
        if (!targetId)
            return;
        const frame = requestAnimationFrame(() => reviewPanel.current?.scrollIntoView({ block: "nearest", behavior: "auto" }));
        return () => cancelAnimationFrame(frame);
    }, [targetId]);
    const candidates = useMemo(() => (query.trim() ? searchMatches(state, {}, query) : state.matches.filter(match => match.assignment).sort((a, b) => a.assignment!.start - b.assignment!.start))
        .filter(match => match.id !== selected.id && match.assignment), [state, selected.id, query]);
    const plan = useMemo(() => targetId ? reviewSwap(state, selected.id, targetId) : null, [state, selected.id, targetId]);
    const timelines = useMemo(() => playerTimelines(state, selected.id), [state, selected.id]);
    const target = state.matches.find(match => match.id === targetId);
    const courtName = (id: string) => state.courts.find(row => row.id === id)?.name ?? id;
    const point = (assignment: Match["assignment"]) => assignment ? `${localTime(assignment.start).replace("T", " ")} · ${courtName(assignment.court)}` : "未排程";
    function close() { if (busy)
        return; dialog.current?.close(); onClose(); }
    function locate(match: Match) { if (busy)
        return; close(); onLocate(match); }
    function apply() {
        try {
            const current = reviewSwap(state, selected.id, targetId);
            if (!current.can_apply)
                throw new Error(current.error ?? "交換仍有衝突，請選其他場次。");
            onSwap(current.updates, selected.id);
            close();
        }
        catch (value) {
            setError(value instanceof Error ? value.message : "無法加入交換草案。");
        }
    }
    function neighbor(label: string, value: Neighbor | null) {
        return <div className="player-neighbor"><span>{label}</span>{value ? <><button type="button" disabled={busy} onClick={() => locate(value.match)}><strong>第 {value.match.code} 場 · {value.match.division}</strong><span>{point(value.match.assignment)}</span><small>{value.match.side_a} 對 {value.match.side_b} · 點選定位 →</small></button><p className={value.sufficient ? "rest-ok" : "rest-warning"}>{value.gap < 0 ? `重疊 ${-value.gap} 分鐘` : `休息 ${value.gap} 分鐘`} · 至少需要 {value.required} 分鐘{value.sufficient ? " · 符合" : " · 不足"}</p></> : <p className="neighbor-empty">沒有其他已排程的{label}。</p>}</div>;
    }
    return <dialog ref={dialog} className="match-tools-dialog" aria-labelledby="match-tools-title" onCancel={event => { event.preventDefault(); close(); }}><div className="sheet-dialog-heading"><h2 id="match-tools-title">第 {selected.code} 場</h2><button type="button" disabled={busy} aria-label="關閉場次工具" onClick={close}>×</button></div><p className="tools-identity">{selected.side_a} 對 {selected.side_b}</p><p className="tools-current">{point(selected.assignment)}</p><div className="tools-tabs" aria-label="場次工具"><button type="button" aria-pressed={mode === "swap"} onClick={() => onMode("swap")}>兩場交換位置</button><button type="button" aria-pressed={mode === "players"} onClick={() => onMode("players")}>選手前後場次</button></div>
    {mode === "swap" ? <section aria-label="交換場次"><p className="tools-note">依目前草案交換「開賽時間與場地」，每場仍保留原本的比賽時間與緩衝。通過整份賽程檢查後才可加入草案；最後仍須檢查並確認儲存。</p><label className="swap-search">找要交換的場次<input type="search" value={query} onChange={event => { setQuery(event.target.value); setLimit(20); setTargetId(""); setError(""); }} placeholder="輸入場號、選手或組別"/></label><div className="swap-targets" aria-label="可選的交換場次">{candidates.slice(0, limit).map(match => <button type="button" key={match.id} disabled={busy || !editable(match)} aria-pressed={targetId === match.id} onClick={() => { setTargetId(match.id); setError(""); }}><strong>第 {match.code} 場 · {match.side_a} 對 {match.side_b}</strong><span>{point(match.assignment)}</span><small>{match.division}{!editable(match) ? " · 已開打、完成或鎖定" : ""}</small></button>)}{!candidates.length && <p>沒有符合的已排程場次。</p>}{candidates.length > limit && <button type="button" onClick={() => setLimit(limit + 20)}>顯示更多（還有 {candidates.length - limit} 場）</button>}</div>
    {plan && target && <section ref={reviewPanel} className="swap-review" aria-label="交換前後比較"><h3 role="status">{plan.can_apply ? "草案檢查通過，尚未儲存" : "無法交換，請先處理限制"}</h3>{[selected, target].map(match => <article key={match.id}><strong>第 {match.code} 場 · {match.side_a} 對 {match.side_b}</strong><p>原：{point(match.assignment)}</p><p className="proposed">→ {point(plan.updates.find(update => update.id === match.id) ?? match.assignment)}</p><small>比賽 {match.play} 分鐘＋場地緩衝 {match.buffer} 分鐘</small></article>)}{plan.error && <p className="conflict">{plan.error}</p>}{plan.issues.length > 0 && <ul className="swap-issues">{plan.issues.map(issue => <li key={issue.key}>{issue.message}</li>)}</ul>}</section>}{error && <p className="conflict" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" disabled={busy} onClick={close}>返回時間表</button><button type="button" className="primary" disabled={busy || !plan?.can_apply} onClick={apply}>加入交換草案</button></div></section> : <section aria-label="已確定選手的前後場次"><p className="tools-note">依目前草案與預定比賽時間估算；休息從比賽結束起算，不含場地整理緩衝。跨組場次會一起檢查。</p>{selected.players.length < 2 && <p className="pending-players">部分或全部對手尚未確定，僅顯示已確定選手；不預估晉級路徑。</p>}{!selected.assignment && <p className="pending-players">本場尚未排程，無法計算前後休息間隔。</p>}{timelines.map(player => <article className="player-timeline" key={player.id}><h3>{player.name}</h3>{neighbor("上一場", player.previous)}<div className="timeline-current"><b>本場 · 第 {selected.code} 場</b><span>{point(selected.assignment)}</span></div>{neighbor("下一場", player.next)}</article>)}{!timelines.length && <p className="neighbor-empty">目前沒有已確定的出賽選手。</p>}<div className="dialog-actions"><button type="button" onClick={close}>返回時間表</button></div></section>}
  </dialog>;
}
