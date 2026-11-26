"use client";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { defaultScoringRules, resultDescription, validateResult } from "../lib/scoring";
import type { ScoringResult, ScoringRules } from "../lib/scoring";

export type QuickMatch = {
    id: string; code: string; division: string; side_a: string; side_b: string;
    planned_start: string | null; scoring_rules?: ScoringRules;
    allowed_outcomes?: NonNullable<ScoringResult["outcome"]>[];
    result?: ScoringResult | null; eligible?: boolean; blocked_reason?: string | null;
};
const signature = (match: QuickMatch) => JSON.stringify([match.side_a, match.side_b, match.scoring_rules, match.result ?? null]);
export default function QuickResultRow({ match, busy, canCorrect, onSave }: {
    match: QuickMatch; busy: boolean; canCorrect: boolean;
    onSave: (match: QuickMatch, result: ScoringResult, expected: ScoringResult | null) => Promise<void>;
}) {
    const [editing, setEditing] = useState(!match.result), [error, setError] = useState(""), [submitting, setSubmitting] = useState(false);
    const [outcome, setOutcome] = useState<"played" | "walkover">(match.result?.outcome === "walkover" ? "walkover" : "played");
    const [conflict, setConflict] = useState(false), [generation, setGeneration] = useState(0);
    const baseline = useRef({ signature: signature(match), result: match.result ?? null });
    const dirty = useRef(false), form = useRef<HTMLFormElement>(null);
    const rules = match.scoring_rules ?? defaultScoringRules;
    useEffect(() => {
        if (signature(match) === baseline.current.signature) return;
        if (dirty.current) { setConflict(true); return; }
        baseline.current = { signature: signature(match), result: match.result ?? null };
        setOutcome(match.result?.outcome === "walkover" ? "walkover" : "played");
        setGeneration(value => value + 1); setEditing(!match.result); setError("");
    }, [match]);
    function reset() {
        dirty.current = false; baseline.current = { signature: signature(match), result: match.result ?? null };
        setConflict(false); setError(""); setOutcome(match.result?.outcome === "walkover" ? "walkover" : "played"); setGeneration(value => value + 1);
    }
    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault(); setError("");
        if (conflict || busy || submitting) return;
        setSubmitting(true);
        const values = new FormData(event.currentTarget), games: {side_a:number;side_b:number}[] = [];
        let blank = false;
        try {
            if (outcome === "played") for (let index = 0; index < rules.best_of; index++) {
                const a = String(values.get(`a-${index}`) ?? ""), b = String(values.get(`b-${index}`) ?? "");
                if (!a && !b) { blank = true; continue; }
                if (blank || !/^\d{1,3}$/.test(a) || !/^\d{1,3}$/.test(b)) throw new Error("請依序填寫各局雙方分數，未打的局留空。");
                games.push({ side_a: Number(a), side_b: Number(b) });
            }
            const winner = outcome === "walkover" ? values.get("winner") === "a" ? "a" : values.get("winner") === "b" ? "b" : null
                : games.filter(game => game.side_a > game.side_b).length > games.filter(game => game.side_b > game.side_a).length ? "a" : "b";
            const result = validateResult({ rules, outcome, winner, games });
            await onSave(match, result, baseline.current.result);
            dirty.current = false; baseline.current = { signature: signature({...match, result}), result }; setConflict(false); setEditing(false);
        } catch (error) { setError(error instanceof Error ? error.message : "儲存失敗，已保留輸入的分數。"); }
        finally { setSubmitting(false); }
    }
    const disabled = busy || conflict || !match.result && match.eligible === false;
    return <article className={"quick-result-row" + (match.result ? " is-completed" : "")}>
        <div className="quick-match-label"><p>第 {match.code} 場 · {match.division}</p><h3><span>{match.side_a}</span><span className="quick-versus">對</span><span>{match.side_b}</span></h3><p>{rules.best_of === 5 ? "五局三勝" : "三局兩勝"}{match.planned_start ? ` · 參考 ${new Date(match.planned_start).toLocaleTimeString("zh-TW",{timeZone:"Asia/Taipei",hour:"2-digit",minute:"2-digit",hour12:false})}` : ""}</p></div>
        {match.result && <p className="quick-current-result">{resultDescription(match.result, match.side_a, match.side_b)}</p>}
        {!editing && match.result && canCorrect && <button type="button" disabled={busy} onClick={() => { reset(); setEditing(true); }}>更正成績</button>}
        {editing && <form ref={form} key={generation} className="quick-score-form" aria-label={`第 ${match.code} 場${match.result ? "更正" : "登錄"}成績`} onSubmit={event => void submit(event)} onChange={() => { dirty.current = true; setError(""); }}>
            <fieldset disabled={disabled}><legend className="sr-only">第 {match.code} 場各局比分</legend>
                <label className="quick-outcome"><span className="sr-only">結果</span><select aria-label={`第 ${match.code} 場結果`} value={outcome} onChange={event => setOutcome(event.target.value as "played" | "walkover")}><option value="played">局分</option><option value="walkover" disabled={!match.allowed_outcomes?.includes("walkover")}>選手棄賽</option></select></label>
                {outcome === "played" ? <div className="quick-games">{Array.from({length:rules.best_of},(_,index) => <div className="quick-game" key={index}><span>第 {index+1} 局</span><div><input name={`a-${index}`} aria-label={`第 ${index+1} 局 ${match.side_a}`} inputMode="numeric" pattern="[0-9]{1,3}" maxLength={3} defaultValue={match.result?.games[index]?.side_a ?? ""}/><span>：</span><input name={`b-${index}`} aria-label={`第 ${index+1} 局 ${match.side_b}`} inputMode="numeric" pattern="[0-9]{1,3}" maxLength={3} defaultValue={match.result?.games[index]?.side_b ?? ""}/></div></div>)}</div>
                    : <label className="quick-winner">棄賽後勝方<select name="winner" required defaultValue={match.result?.winner ?? ""}><option value="">選擇勝方</option><option value="a">{match.side_a}</option><option value="b">{match.side_b}</option></select></label>}
                <button className="primary" type="submit">{submitting ? "儲存中…" : match.result ? "儲存更正" : "登錄"}</button>
                {match.result && <button type="button" onClick={() => {reset();setEditing(false);}}>取消</button>}
            </fieldset>
            {match.blocked_reason && !match.result && <p className="quick-row-note">{match.blocked_reason}</p>}
            {error && <p role="alert" className="notice error">{error}</p>}
            {conflict && <p role="alert" className="notice error">此場對戰或結果已更新，輸入仍保留。<button type="button" disabled={busy} onClick={reset}>依最新資料重新填寫</button></p>}
        </form>}
    </article>;
}
