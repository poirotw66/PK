import { readTournament } from "./schedule/schema";
import type { Tournament } from "./schedule/schema";
import { defaultScoringRules, validateResult } from "./scoring";
import type { ScoringResult } from "./scoring";

type Source = { kind: string; match_id?: string; player_id?: string; entry_id?: string; stage_id?: string; pool_id?: string; rank?: number };
type ResolvedMatch = Tournament["matches"][number] & { slots?: { a: Source; b: Source }; entry_ids?: { a: string | null; b: string | null } };
function normalized(result: ScoringResult) { return { rules: result.rules, winner: result.winner, outcome: result.outcome ?? "played", games: result.games }; }
export function reviewResultChange(state: Tournament, matchId: string, proposed: unknown) {
    const next = structuredClone(state), match = next.matches.find(row => row.id === matchId);
    if (!match) throw new Error("找不到這場比賽。");
    if (match.players.length !== 2) throw new Error("雙方選手尚未確認，不能登錄成績。");
    const after = validateResult(proposed), rules = match.scoring_rules ?? defaultScoringRules;
    if (!(match.allowed_outcomes ?? ["played"]).includes(after.outcome ?? "played")) throw new Error("此場尚未核定此類特殊結果規則。請先在地端補齊條文並核對規則來源，再登錄。");
    if (after.rules.best_of !== rules.best_of || after.rules.target !== rules.target || after.rules.margin !== rules.margin)
        throw new Error("成績計分規則與此場比賽不一致。");
    const before = match.result ?? null, correction = !!before;
    if (match.status === "completed" && !before) throw new Error("此場已有完賽狀態但缺少結果，請先核對地端資料。");
    if (before && JSON.stringify(normalized(before)) === JSON.stringify(normalized(after))) throw new Error("成績沒有變更。");
    const invalidated: string[] = [];
    if (correction) {
        if (next.encouragement_draws?.some(draw => draw.source_match_ids.includes(matchId))) throw new Error("此場已作為鼓勵賽資格與抽籤依據，不能直接更正；請先核對鼓勵賽籤表及相關結果。");
        const affected = new Set([matchId]);
        const metadata = next as Tournament & { entries?: { id: string; player_id: string }[]; advancement_pools?: { id: string; match_ids: string[] }[] };
        const entryPlayers = new Map(metadata.entries?.map(entry => [entry.id, entry.player_id]) ?? []);
        for (let changed = true; changed;) {
            changed = false;
            for (const candidate of next.matches as ResolvedMatch[]) {
                if (affected.has(candidate.id)) continue;
                const sources = candidate.slots ? Object.values(candidate.slots) : [];
                if (candidate.dependencies.some(dep => affected.has(dep.id)) || sources.some(source => source.match_id && affected.has(source.match_id) || source.kind === "pool_rank" && metadata.advancement_pools?.find(pool => pool.id === source.pool_id)?.match_ids.some(id => affected.has(id)))) {
                    affected.add(candidate.id); changed = true;
                }
            }
        }
        for (const candidate of next.matches as ResolvedMatch[]) {
            if (candidate.id === matchId || !affected.has(candidate.id)) continue;
            if (candidate.status === "in_progress" || candidate.status === "completed" || candidate.result || candidate.lock_time || candidate.lock_court)
                throw new Error(`第 ${candidate.code} 場已開打、完賽或鎖定，不能自動更正上游成績。`);
            if (!candidate.slots && candidate.players.length) throw new Error(`第 ${candidate.code} 場缺少明確晉級來源，請先核對地端賽程後再更正。`);
            // A resolved future entrant must not survive a changed source result.
            // The advancement preview resolves it again from current confirmed results.
            if (candidate.slots) {
                let reset = false;
                for (const side of ["a", "b"] as const) {
                    const source = candidate.slots[side];
                    if (source.kind === "entry") continue;
                    candidate[side === "a" ? "side_a" : "side_b"] = source.kind === "winner" ? "來源場次勝者待確認" : source.kind === "loser" ? "來源場次敗者待確認" : "小組名次待確認";
                    if (candidate.entry_ids) candidate.entry_ids[side] = null;
                    reset = true;
                }
                if (reset) {
                    candidate.players = Object.values(candidate.slots).flatMap(source => { const player = source.kind === "entry" && source.entry_id ? entryPlayers.get(source.entry_id) : null; return player ? [player] : []; });
                    candidate.status = "planned";
                    invalidated.push(candidate.id);
                }
            }
        }
        // Explicit pool decisions are bound to the old results and must be reviewed again.
        const invalidPools = new Set(metadata.advancement_pools?.filter(pool => pool.match_ids.some(id => affected.has(id))).map(pool => pool.id) ?? []);
        if (next.advancement_decisions) next.advancement_decisions = next.advancement_decisions.filter(decision => !invalidPools.has(decision.pool_id));
    }
    match.result = after;
    match.status = "completed";
    return { state: readTournament(next), before, after, correction, invalidated };
}
