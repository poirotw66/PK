import type { FormEvent } from "react";
import type { Game, ScoringRules } from "../lib/scoring";

export default function DirectResult({ match, busy, onClose, onSubmit }: {
    match: { code: string; side_a: string; side_b: string; scoring_rules?: ScoringRules };
    busy: boolean;
    onClose: () => void;
    onSubmit: (games: Game[]) => void;
}) {
    const count = match.scoring_rules?.best_of ?? 3;
    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const values = new FormData(event.currentTarget);
        const games: Game[] = [];
        let blank = false;
        const form = event.currentTarget;
        const report = (message: string) => {
            const feedback = form.querySelector("[role=alert]");
            if (feedback) feedback.textContent = message;
        };
        for (let index = 0; index < count; index++) {
            const a = String(values.get(`a-${index}`) ?? "");
            const b = String(values.get(`b-${index}`) ?? "");
            if (!a && !b) { blank = true; continue; }
            if (blank || !/^\d{1,3}$/.test(a) || !/^\d{1,3}$/.test(b)) {
                report("請依序填寫各局的兩個分數，未打的局留空。");
                return;
            }
            games.push({ side_a: Number(a), side_b: Number(b) });
        }
        if (games.length < 2) { report("請輸入整場的完賽局分。"); return; }
        report("");
        if (window.confirm(`確定送出完賽結果：${games.map(game => `${game.side_a}:${game.side_b}`).join("、")}？`))
            onSubmit(games);
    }
    return <form className="live-confirm" aria-label="直接登錄完賽" onSubmit={submit}>
        <h3>第 {match.code} 場 · 直接登錄完賽</h3>
        <p>依順序填寫已打完的局，未打的局留空。每局 {match.scoring_rules?.target ?? 11} 分，須領先兩分。</p>
        <fieldset disabled={busy}>
            <legend>各局比分</legend>
            {Array.from({length: count}, (_, index) => <fieldset key={index}>
                <legend>第 {index + 1} 局</legend>
                <label>{match.side_a}<input name={`a-${index}`} inputMode="numeric" pattern="[0-9]{1,3}" maxLength={3}/></label>
                <label>{match.side_b}<input name={`b-${index}`} inputMode="numeric" pattern="[0-9]{1,3}" maxLength={3}/></label>
            </fieldset>)}
        </fieldset>
        <p role="alert" />
        <button type="button" disabled={busy} onClick={onClose}>返回</button>
        <button className="primary" disabled={busy}>{busy ? "送出中…" : "送出完賽結果"}</button>
    </form>;
}
