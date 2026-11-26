import { validateEncouragementRecords } from "../encouragement-record";
import { z } from "zod";
import { resultSchema, scoringRulesSchema, validateResult } from "../scoring";
const id = z.string().min(1).max(100);
const minute = z.number().int().nonnegative();
const point = z.object({ start: minute, end: minute }).strict().refine(v => v.end > v.start);
export const assignmentSchema = z.object({ start: minute, court: id }).strict();
export const slotSourceSchema = z.discriminatedUnion("kind", [
    z.object({ kind: z.literal("entry"), entry_id: id }).strict(),
    z.object({ kind: z.literal("winner"), match_id: id }).strict(),
    z.object({ kind: z.literal("loser"), match_id: id }).strict(),
    z.object({ kind: z.literal("pool_rank"), pool_id: id, rank: z.number().int().min(1).max(2000) }).strict(),
    z.object({ kind: z.literal("pending") }).strict(),
]);
export const poolOrderingSchema = z.object({ stage_id: id, pool_id: id, entry_ids: z.array(id).min(1).max(2000) }).strict();
export const advancementDecisionSchema = poolOrderingSchema.extend({ basis: z.string().min(1).max(1000000) }).strict();
export type SlotSource = z.infer<typeof slotSourceSchema>;
const encouragementIdentity = z.object({ entry_id: id, player_id: id, name: z.string().min(1).max(200) }).strict();
export const encouragementSourcesSchema = z.object({ source_revision: id, divisions: z.array(z.object({
    division_id: id, division: z.string().min(1).max(100), stage_id: id,
    first_round: z.array(z.object({ match_id: id, side_a: encouragementIdentity, side_b: encouragementIdentity }).strict()).min(1).max(500),
}).strict()).max(1000) }).strict();
export const encouragementDrawSchema = z.object({
    id, division_id: id, division: z.string().min(1).max(100), source_stage_id: id,
    source_match_ids: z.array(id).min(1).max(500), seed: z.number().int().min(0).max(4294967295),
    basis: z.string().min(1).max(1000000), entry_ids: z.array(id).max(500), bracket_entry_ids: z.array(id.nullable()).max(1024),
    match_ids: z.array(id).max(500), generated_at: z.string().datetime(),
}).strict();
export const tournamentSchema = z.object({
    format: z.literal("pk-schedule-v1"),
    source_revision: id,
    name: z.string().min(1).max(200),
    timezone: z.literal("Asia/Taipei"),
    dates: z.array(z.string().regex(/^\d{4}-\d{2}-\d{2}$/)).min(1).max(10),
    grid: z.number().int().min(1).max(60),
    anchors: z.record(minute),
    first_start: z.literal("08:00"),
    last_start: z.literal("17:30"),
    minimum_rest: z.literal(60),
    daily_limit: z.literal(5),
    participant_scope: z.literal("confirmed_only"),
    keep_groups_same_day: z.boolean(),
    courts: z.array(z.object({ id, name: z.string().min(1).max(100), windows: z.array(point).min(1).max(30) }).strict()).min(1).max(20),
    players: z.array(z.object({ id, name: z.string().min(1).max(100), rest: minute }).strict()).max(2000),
    entries: z.array(z.object({ id, player_id: id }).strict()).max(4000).optional(),
    advancement_pools: z.array(z.object({ id, stage_id: id, label: z.string().max(200), entry_ids: z.array(id).min(1).max(2000), match_ids: z.array(id).max(1000) }).strict()).max(1000).optional(),
    advancement_decisions: z.array(advancementDecisionSchema).max(1000).optional(),
    encouragement_sources: encouragementSourcesSchema.optional(),
    encouragement_draws: z.array(encouragementDrawSchema).max(1000).optional(),
    coach_profile: z.object({ version: z.literal(1), championship_ids: z.array(id).max(1000).refine(values => new Set(values).size === values.length) }).strict().optional(),
    matches: z.array(z.object({
        id, code: z.string().min(1).max(50), division: z.string().min(1).max(100),
        stage: z.string().max(100), group: z.string().max(100).nullable(),
        side_a: z.string().max(200), side_b: z.string().max(200),
        slots: z.object({ a: slotSourceSchema, b: slotSourceSchema }).strict().optional(),
        entry_ids: z.object({ a: id.nullable(), b: id.nullable() }).strict().optional(),
        allowed_outcomes: z.array(z.enum(["played", "walkover", "retired", "double_forfeit"])).min(1).max(4).refine(values => values.includes("played") && new Set(values).size === values.length).optional(),
        championship: z.boolean().optional(),
        encouragement: z.object({draw_id:id,division_id:id,stage_id:id,round:z.number().int().min(1).max(20),position:z.number().int().nonnegative()}).strict().optional(),
        scoring_rules: scoringRulesSchema.optional(), result: resultSchema.optional(),
        players: z.array(id).max(2), status: z.enum(["planned", "ready", "in_progress", "completed"]),
        play: z.number().int().min(1).max(240), buffer: minute, rest: minute, transfer: minute,
        dependencies: z.array(z.object({ id, lag: minute }).strict()).max(500),
        ready_after: minute.nullable(),
        lock_time: z.boolean(), lock_court: z.boolean(),
        assignment: assignmentSchema.nullable(),
    }).strict()).min(1).max(1000),
}).strict();
export type Tournament = z.infer<typeof tournamentSchema>;
export type Match = Tournament["matches"][number];
export const updatesSchema = z.array(z.object({ id, start: minute, court: id }).strict()).min(1).max(500);
export type Update = z.infer<typeof updatesSchema>[number];
export function readTournament(input: unknown): Tournament {
    const state = tournamentSchema.parse(input);
    for (const values of [state.courts, state.players, state.matches]) {
        if (new Set(values.map(v => v.id)).size !== values.length)
            throw new Error("資料包含重複識別碼。");
    }
    const players = new Set(state.players.map(p => p.id)), matches = new Set(state.matches.map(m => m.id));
    const courts = new Set(state.courts.map(c => c.id));
    if (state.coach_profile?.championship_ids.some(id => !matches.has(id))) throw new Error("冠軍賽設定參照不存在的場次。");
    for (const match of state.matches) {
        if (state.coach_profile && match.championship !== undefined && match.championship !== state.coach_profile.championship_ids.includes(match.id)) throw new Error("冠軍賽設定與場次標記不一致。");
        if (match.result) {
            validateResult(match.result);
            if (match.status !== "completed") throw new Error("有成績的比賽必須為已完賽狀態。");
        }
        if (match.players.some(p => !players.has(p)) || new Set(match.players).size !== match.players.length
            || match.dependencies.some(d => !matches.has(d.id) || d.id === match.id)
            || match.assignment && !courts.has(match.assignment.court))
            throw new Error("場次參照資料不完整。");
    }
    const entries = new Map((state.entries ?? []).map(entry => [entry.id, entry]));
    const draws = state.encouragement_draws ?? [];
    if (new Set(draws.map(draw=>draw.id)).size !== draws.length || new Set(draws.map(draw=>draw.division_id)).size !== draws.length) throw new Error("鼓勵賽組別重複。");
    for (const draw of draws) {
        if (new Set(draw.entry_ids).size !== draw.entry_ids.length || new Set(draw.match_ids).size !== draw.match_ids.length || draw.match_ids.length !== Math.max(0,draw.entry_ids.length-1) || draw.source_match_ids.some(id=>!matches.has(id)) || draw.match_ids.some(id=>!matches.has(id)) || draw.entry_ids.some(id=>!entries.has(id))) throw new Error("鼓勵賽籤表參照不完整。");
    }
    for (const match of state.matches) if (match.encouragement && !draws.some(draw=>draw.id===match.encouragement!.draw_id && draw.division_id===match.encouragement!.division_id && draw.division===match.division && draw.match_ids.includes(match.id))) throw new Error("鼓勵賽場次缺少已核定籤表。");
    const pools = new Map((state.advancement_pools ?? []).map(pool => [pool.id, pool]));
    if (entries.size !== (state.entries?.length ?? 0) || pools.size !== (state.advancement_pools?.length ?? 0)) throw new Error("晉級來源含重複識別碼。");
    for (const entry of entries.values()) if (!players.has(entry.player_id)) throw new Error("晉級來源選手不存在。");
    for (const pool of pools.values()) {
        if (new Set(pool.entry_ids).size !== pool.entry_ids.length || new Set(pool.match_ids).size !== pool.match_ids.length || pool.entry_ids.some(entry => !entries.has(entry)) || pool.match_ids.some(match => !matches.has(match))) throw new Error("小組晉級來源不完整。");
    }
    const map = new Map(state.matches.map(match => [match.id, match]));
    const visited = new Set<string>();
    function visit(matchId: string, seen: Set<string>) {
        if (seen.has(matchId)) throw new Error("晉級來源形成循環。");
        if (visited.has(matchId)) return;
        const match = map.get(matchId)!;
        for (const slot of Object.values(match.slots ?? {})) {
            if (slot.kind === "entry" && !entries.has(slot.entry_id) || slot.kind === "pool_rank" && (!pools.has(slot.pool_id) || slot.rank > pools.get(slot.pool_id)!.entry_ids.length)) throw new Error("晉級來源參照不完整。");
            if (slot.kind === "winner" || slot.kind === "loser") {
                if (!matches.has(slot.match_id)) throw new Error("晉級來源場次不存在。");
                visit(slot.match_id, new Set([...seen, matchId]));
            }
        }
        visited.add(matchId);
    }
    for (const match of state.matches) {
        visit(match.id, new Set());
        for (const entryId of Object.values(match.entry_ids ?? {})) if (entryId !== null && !entries.has(entryId)) throw new Error("已核定晉級選手不存在。");
        if (match.entry_ids?.a && match.entry_ids.a === match.entry_ids.b) throw new Error("雙方不能是同一筆參賽資料。");
        if (match.entry_ids) {
            const bound: string[] = [];
            for (const side of ["a", "b"] as const) {
                const entryId = match.entry_ids[side];
                if (!entryId) continue;
                const entry = entries.get(entryId)!, player = state.players.find(player => player.id === entry.player_id)!;
                if (match[side === "a" ? "side_a" : "side_b"] !== player.name) throw new Error("已核定晉級身分與顯示姓名不一致。");
                bound.push(player.id);
            }
            if (new Set(bound).size !== bound.length || JSON.stringify(bound.sort()) !== JSON.stringify([...match.players].sort())) throw new Error("已核定晉級身分與實際選手不一致。");
        }
    }
    for (const decision of state.advancement_decisions ?? []) {
        const pool = pools.get(decision.pool_id);
        if (!pool || pool.stage_id !== decision.stage_id || new Set(decision.entry_ids).size !== decision.entry_ids.length || decision.entry_ids.length !== pool.entry_ids.length || decision.entry_ids.some(entry => !pool.entry_ids.includes(entry))) throw new Error("人工名次須包含同組每位選手一次。");
    }
    validateEncouragementRecords(state);
    if (state.dates.some(d => !Number.isInteger(state.anchors[d])))
        throw new Error("比賽日期缺少時間基準。");
    return state;
}
