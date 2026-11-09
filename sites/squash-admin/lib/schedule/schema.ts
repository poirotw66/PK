import { z } from "zod";
import { resultSchema, scoringRulesSchema, validateResult } from "../scoring";
const id = z.string().min(1).max(100);
const minute = z.number().int().nonnegative();
const point = z.object({ start: minute, end: minute }).strict().refine(v => v.end > v.start);
export const assignmentSchema = z.object({ start: minute, court: id }).strict();
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
    matches: z.array(z.object({
        id, code: z.string().min(1).max(50), division: z.string().min(1).max(100),
        stage: z.string().max(100), group: z.string().max(100).nullable(),
        side_a: z.string().max(200), side_b: z.string().max(200),
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
    for (const match of state.matches) {
        if (match.result) {
            validateResult(match.result);
            if (match.status !== "completed") throw new Error("有成績的比賽必須為已完賽狀態。");
        }
        if (match.players.some(p => !players.has(p)) || new Set(match.players).size !== match.players.length
            || match.dependencies.some(d => !matches.has(d.id) || d.id === match.id)
            || match.assignment && !courts.has(match.assignment.court))
            throw new Error("場次參照資料不完整。");
    }
    if (state.dates.some(d => !Number.isInteger(state.anchors[d])))
        throw new Error("比賽日期缺少時間基準。");
    return state;
}
