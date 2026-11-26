import { z } from "zod";
import { readTournament, tournamentSchema, poolOrderingSchema } from "./schedule/schema";
import type { Tournament, Match, SlotSource } from "./schedule/schema";
import { validate } from "./schedule/rules";
import { canonicalJson } from "./local-return-model";

export const advancementInput = z.object({ revision: z.string().min(1).max(100), sequence: z.number().int().nonnegative(), reason: z.string().trim().min(1).max(500), pool_orderings: z.array(poolOrderingSchema).max(1000).default([]), source_state: tournamentSchema.optional() }).strict();
type Ordering = z.infer<typeof poolOrderingSchema>;
export type AdvancementChange = { id: string; code: string; before: { side_a: string; side_b: string; players: string[]; entry_ids?: Match["entry_ids"] }; after: { side_a: string; side_b: string; players: string[]; entry_ids: NonNullable<Match["entry_ids"]> } };
export function poolBasis(state: Tournament, poolId: string) {
    const pool = state.advancement_pools?.find(pool => pool.id === poolId);
    if (!pool) throw new Error("找不到晉級小組。");
    return canonicalJson(pool.match_ids.map(id => {
        const match = state.matches.find(match => match.id === id)!;
        return { id, status: match.status, result: match.result ?? null };
    }).sort((a, b) => a.id.localeCompare(b.id)));
}
function resolve(state: Tournament, source: SlotSource, seen: Set<string>): string | null {
    if (source.kind === "entry") return source.entry_id;
    if (source.kind === "pending") return null;
    if (source.kind === "pool_rank") {
        const pool = state.advancement_pools?.find(pool => pool.id === source.pool_id);
        const decision = state.advancement_decisions?.find(decision => decision.pool_id === source.pool_id && decision.basis === poolBasis(state, source.pool_id));
        return pool && decision && pool.match_ids.every(id => state.matches.some(match => match.id === id && match.status === "completed" && !!match.result)) ? decision.entry_ids[source.rank - 1] ?? null : null;
    }
    if (seen.has(source.match_id)) return null;
    const match = state.matches.find(match => match.id === source.match_id);
    if (!match?.slots || match.status !== "completed" || !match.result?.winner) return null;
    const side = source.kind === "winner" ? match.result.winner : match.result.winner === "a" ? "b" : "a";
    return resolve(state, match.slots[side], new Set([...seen, source.match_id]));
}
function candidateEntryIds(state: Tournament, match: Match) {
    return { a: match.slots ? resolve(state, match.slots.a, new Set([match.id])) : null, b: match.slots ? resolve(state, match.slots.b, new Set([match.id])) : null };
}
export function resolvedEntryIds(state: Tournament, match: Match) {
    const ids = match.entry_ids ?? { a: match.slots?.a.kind === "entry" ? match.slots.a.entry_id : null, b: match.slots?.b.kind === "entry" ? match.slots.b.entry_id : null };
    const bound = (side: "a" | "b") => {
        const entry = state.entries?.find(entry => entry.id === ids[side]);
        const player = entry && state.players.find(player => player.id === entry.player_id);
        return player && match.players.includes(player.id) && match[side === "a" ? "side_a" : "side_b"] === player.name ? entry!.id : null;
    };
    return { a: bound("a"), b: bound("b") };
}
export function attachAdvancementSources(current: Tournament, input: Tournament) {
    const source = readTournament(input), state = structuredClone(current);
    if (!source.entries || !source.matches.some(match => match.slots)) throw new Error("交接檔沒有晉級來源，請先用新版地端匯出。");
    const metadata = ["entries", "advancement_pools", "advancement_decisions"];
    const event = (value: Tournament) => Object.fromEntries(Object.entries(value).filter(([key]) => key !== "matches" && !metadata.includes(key)));
    if (canonicalJson(event(state)) !== canonicalJson(event(source))) throw new Error("來源版本、賽事規則或名單與雲端不一致，不能補入晉級來源。");
    if (state.matches.length !== source.matches.length) throw new Error("晉級來源場次數不一致。");
    const definition = (match: Match) => Object.fromEntries(Object.entries(match).filter(([key]) => !["assignment", "status", "result", "side_a", "side_b", "players", "slots", "entry_ids", "allowed_outcomes", "code"].includes(key)));
    const entries = new Map(source.entries.map(entry => [entry.id, entry]));
    for (const match of state.matches) {
        const original = source.matches.find(candidate => candidate.id === match.id);
        if (!original || canonicalJson(definition(match)) !== canonicalJson(definition(original)) || !original.slots) throw new Error("晉級來源與既有場次、規則或依賴不一致。");
        if (match.slots && canonicalJson(match.slots) !== canonicalJson(original.slots)) throw new Error("已綁定的晉級來源不能改寫。");
        const binding = (key: "a" | "b") => {
            const slot = original.slots![key], id = slot.kind === "entry" ? slot.entry_id : original.entry_ids?.[key];
            const entry = id && entries.get(id), player = entry && state.players.find(player => player.id === entry.player_id);
            if (player && match.players.includes(player.id) && match[key === "a" ? "side_a" : "side_b"] === player.name) return entry.id;
            if (slot.kind === "entry") throw new Error("固定參賽選手身分與雲端不一致。");
            return null;
        };
        const ids = match.entry_ids ?? { a: binding("a"), b: binding("b") };
        const people = Object.values(ids).filter((id): id is string => !!id).map(id => entries.get(id)?.player_id).sort();
        if (canonicalJson(people) !== canonicalJson([...match.players].sort())) throw new Error("無法核對現有對戰選手，不能補入來源。");
        match.slots = structuredClone(original.slots); match.entry_ids = ids;
        match.allowed_outcomes = original.allowed_outcomes ?? ["played"];
    }
    state.entries = structuredClone(source.entries);
    state.advancement_pools = structuredClone(source.advancement_pools ?? []);
    state.advancement_decisions ??= [];
    return readTournament(state);
}
export function reviewAdvancement(current: Tournament, orderings: Ordering[] = [], source?: Tournament) {
    const state = source ? attachAdvancementSources(current, source) : readTournament(structuredClone(current));
    const sourceAttached = source ? canonicalJson(current) !== canonicalJson(state) : false;
    const decisions = new Map((state.advancement_decisions ?? []).map(decision => [decision.pool_id, decision]));
    const duplicates = new Set<string>();
    for (const ordering of orderings) {
        if (duplicates.has(ordering.pool_id)) throw new Error("同一小組不可重複核定名次。");
        duplicates.add(ordering.pool_id);
        const pool = state.advancement_pools?.find(pool => pool.id === ordering.pool_id && pool.stage_id === ordering.stage_id);
        if (!pool || !pool.match_ids.length || pool.match_ids.some(id => state.matches.find(match => match.id === id)?.status !== "completed" || !state.matches.find(match => match.id === id)?.result)) throw new Error("小組場次尚未全部確認成績，不能核定名次。");
        if (ordering.entry_ids.length !== pool.entry_ids.length || new Set(ordering.entry_ids).size !== ordering.entry_ids.length || ordering.entry_ids.some(id => !pool.entry_ids.includes(id))) throw new Error("核定名次必須包含同組每位選手一次。");
        decisions.set(pool.id, { ...ordering, basis: poolBasis(state, pool.id) });
    }
    if (state.advancement_pools) state.advancement_decisions = [...decisions.values()];
    const changes: AdvancementChange[] = [], pending: { id: string; code: string; message: string }[] = [];
    const blockers: { id: string; code: string; message: string }[] = [];
    const entries = new Map((state.entries ?? []).map(entry => [entry.id, entry]));
    for (const match of state.matches) {
        if (!match.slots || !Object.values(match.slots).some(source => ["winner", "loser", "pool_rank"].includes(source.kind))) continue;
        const ids = candidateEntryIds(state, match);
        if (!ids.a || !ids.b) { pending.push({ id: match.id, code: match.code, message: "來源成績或人工名次尚未確認，保留原對戰。" }); continue; }
        const first = entries.get(ids.a), second = entries.get(ids.b);
        if (!first || !second) throw new Error("晉級來源選手不完整。");
        if (first.player_id === second.player_id) { blockers.push({ id: match.id, code: match.code, message: "雙方為同一位實際選手，不能套用晉級。" }); continue; }
        const after = { side_a: state.players.find(player => player.id === first.player_id)!.name, side_b: state.players.find(player => player.id === second.player_id)!.name, players: [first.player_id, second.player_id].sort(), entry_ids: { a: ids.a, b: ids.b } };
        const before = { side_a: match.side_a, side_b: match.side_b, players: [...match.players].sort(), ...(match.entry_ids ? { entry_ids: match.entry_ids } : {}) };
        if (canonicalJson(before) === canonicalJson(after)) continue;
        if (match.status === "completed" || match.status === "in_progress" || match.lock_time || match.lock_court) { blockers.push({ id: match.id, code: match.code, message: "已開賽、完賽或鎖定的場次不可更換晉級選手。" }); continue; }
        Object.assign(match, after);
        changes.push({ id: match.id, code: match.code, before, after });
    }
    const issues = changes.length ? validate(state, new Set(changes.map(change => change.id))) : [];
    const decisionChanged = canonicalJson(current.advancement_decisions ?? []) !== canonicalJson(state.advancement_decisions ?? []);
    return { state, changes, pending, blockers, issues, source_attached: sourceAttached, decision_changed: decisionChanged, can_apply: !blockers.length && !issues.length && (changes.length > 0 || decisionChanged || sourceAttached) };
}
