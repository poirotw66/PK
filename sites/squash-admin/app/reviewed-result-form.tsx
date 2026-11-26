"use client";
import { useState } from "react";
import type { FormEvent } from "react";
import { defaultScoringRules, validateResult } from "../lib/scoring";
import type { Game, ScoringResult, ScoringRules } from "../lib/scoring";
export default function ReviewedResultForm({ match, initial, initialReason = "", correction = !!initial, busy, onClose, onSubmit }: {
    match: { code: string; side_a: string; side_b: string; scoring_rules?: ScoringRules; allowed_outcomes?: NonNullable<ScoringResult["outcome"]>[] };
    initial?: ScoringResult; initialReason?: string; correction?: boolean; busy: boolean; onClose: () => void; onSubmit: (result: ScoringResult, reason: string) => void;
}) {
    const [outcome, setOutcome] = useState<ScoringResult["outcome"]>(initial?.outcome ?? "played");
    const [error, setError] = useState("");
    const rules = match.scoring_rules ?? defaultScoringRules, count = rules.best_of;
    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault(); setError("");
        const values = new FormData(event.currentTarget), games: Game[] = [];
        let blank = false;
        if (outcome === "played" || outcome === "retired") for (let index = 0; index < count; index++) {
            const a = String(values.get(`a-${index}`) ?? ""), b = String(values.get(`b-${index}`) ?? "");
            if (!a && !b) { blank = true; continue; }
            if (blank || !/^\d{1,3}$/.test(a) || !/^\d{1,3}$/.test(b)) { setError("請依序填寫各局的兩個分數，未打的局留空。"); return; }
            games.push({ side_a: Number(a), side_b: Number(b) });
        }
        const winner = outcome === "double_forfeit" ? null : outcome === "played"
            ? games.filter(game => game.side_a > game.side_b).length > games.filter(game => game.side_b > game.side_a).length ? "a" : "b"
            : values.get("winner") === "a" ? "a" : values.get("winner") === "b" ? "b" : null;
        try { onSubmit(validateResult({ rules, outcome, winner, games }), String(values.get("reason") ?? "").trim()); }
        catch (error) { setError(error instanceof Error ? error.message : "請核對成績。"); }
    }
    return <form className="live-confirm" aria-label={correction ? "更正成績" : "登錄成績"} onSubmit={submit}>
        <h3>第 {match.code} 場 · {correction ? "更正成績" : "登錄成績"}</h3><p>{match.side_a} 對 {match.side_b}</p>
        <fieldset disabled={busy}><legend>結果與原因</legend>
            <label>比賽結果<select value={outcome} onChange={event => setOutcome(event.target.value as ScoringResult["outcome"])}><option value="played">正常完賽</option><option value="walkover" disabled={!match.allowed_outcomes?.includes("walkover")}>未賽棄權</option><option value="retired" disabled={!match.allowed_outcomes?.includes("retired")}>傷退</option><option value="double_forfeit" disabled={!match.allowed_outcomes?.includes("double_forfeit")}>雙方棄權</option></select></label>
            {(match.allowed_outcomes ?? ["played"]).length === 1 && <p>尚未核定特殊結果規則，目前僅開放正常完賽。請先在地端補齊條文，再由主管理員核對規則來源。</p>}
            {(outcome === "walkover" || outcome === "retired") && <label>確認勝方<select name="winner" defaultValue={initial?.winner ?? ""} required><option value="">選擇勝方</option><option value="a">{match.side_a}</option><option value="b">{match.side_b}</option></select></label>}
            {(outcome === "played" || outcome === "retired") ? <><p>每局 {rules.target} 分，領先兩分。{outcome === "retired" ? "只填實際比分，最後一局可未完成；未打的局留空。" : "依序填寫完賽局分，未打的局留空。"}</p>{Array.from({ length: count }, (_, index) => <fieldset key={index}><legend>第 {index + 1} 局</legend><label>{match.side_a}<input key={`${outcome}-a-${index}`} name={`a-${index}`} defaultValue={initial?.games[index]?.side_a ?? ""} inputMode="numeric" pattern="[0-9]{1,3}" maxLength={3}/></label><label>{match.side_b}<input key={`${outcome}-b-${index}`} name={`b-${index}`} defaultValue={initial?.games[index]?.side_b ?? ""} inputMode="numeric" pattern="[0-9]{1,3}" maxLength={3}/></label></fieldset>)}</> : <p>{outcome === "double_forfeit" ? "雙方棄權不指定勝方，亦不補造比分。" : "未賽棄權只記錄勝方，不補造比分。"}</p>}
            <label>{correction ? "更正原因" : "登錄依據／特殊結果原因"}<textarea name="reason" defaultValue={initialReason} required maxLength={500} rows={2} placeholder={correction ? "例如：核對簽名成績單後修正第二局" : "例如：依裁判確認成績單登錄"}/></label>
        </fieldset>
        {error && <p role="alert" className="notice error">{error}</p>}
        <div className="dialog-actions"><button type="button" disabled={busy} onClick={onClose}>返回</button><button className="primary" disabled={busy}>{busy ? "正在核對…" : "預覽成績"}</button></div>
    </form>;
}
