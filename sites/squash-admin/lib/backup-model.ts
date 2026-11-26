import { z } from "zod";
import { canonicalDigest, canonicalJson } from "./local-return-model";
import { emptyLive } from "./publication-model";
import type { LiveState } from "./publication-model";
import { readTournament, tournamentSchema } from "./schedule/schema";
import type { Tournament } from "./schedule/schema";
import { validate } from "./schedule/rules";

const identifier = z.string().min(1).max(100);
const timestamp = z.string().max(50).refine(value => Number.isFinite(Date.parse(value)), "備份時間無效。");
const liveSchema = z.object({
    courts: z.record(z.object({ match_id: identifier.nullable(), started_at: timestamp.nullable(), previous_status: z.enum(["planned", "ready"]).nullable() }).strict()),
    queues: z.record(z.array(identifier).max(3)).optional(),
    starts: z.record(timestamp), actual_courts: z.record(identifier).optional(),
}).strict();
const auditSchema = z.object({ id: identifier, parent: identifier.nullable(), actor: z.string().max(500), reason: z.string().max(2000), created: z.number().int().nonnegative() }).strict();
export const backupSchema = z.object({
    format: z.literal("pk-admin-backup-v1"), origin: z.string().url().max(500), exported_at: timestamp,
    cloud_revision: identifier, sequence: z.number().int().nonnegative(), state: tournamentSchema,
    live: liveSchema, history: z.array(auditSchema).max(10000), sha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict();
export type Backup = z.infer<typeof backupSchema>;
export type BackupInput = Omit<Backup, "format" | "sha256" | "live" | "history"> & { live?: LiveState | null; history?: Backup["history"] };
export async function buildBackup(input: BackupInput): Promise<Backup> {
    const state = readTournament(input.state);
    const payload = { format: "pk-admin-backup-v1" as const, ...input, state, live: input.live ?? emptyLive(state), history: input.history ?? [] };
    return backupSchema.parse({ ...payload, sha256: await canonicalDigest(payload) });
}
function identities(state: Tournament) {
    return {
        players: state.players.map(player => ({ id: player.id, name: player.name })).sort((a, b) => a.id.localeCompare(b.id)),
        courts: state.courts.map(court => court.id).sort(),
        matches: state.matches.map(match => ({ id: match.id, code: match.code, division: match.division, stage: match.stage, group: match.group })).sort((a, b) => a.id.localeCompare(b.id)),
    };
}
export async function reviewBackup(input: unknown, origin: string, current: Tournament | null) {
    const backup = backupSchema.parse(input), { sha256, ...payload } = backup;
    if (backup.origin !== origin) throw new Error("備份屬於另一個網站，不能還原至此後台。");
    if (await canonicalDigest(payload) !== sha256) throw new Error("備份校驗失敗，請重新選擇完整備份檔。");
    const state = readTournament(backup.state);
    if (state.matches.some(match => match.status === "in_progress") || Object.values(backup.live.courts).some(court => court.match_id))
        throw new Error("備份仍有進行中的比賽。請使用沒有進行中比賽的備份，逐球歷程不會還原。");
    if (state.matches.some(match => match.status === "completed" && !match.result))
        throw new Error("備份包含尚未確認成績的完賽場次，不能還原。");
    if (current?.matches.some(match => match.status === "in_progress")) throw new Error("仍有比賽進行中，請先結束或撤銷開賽。");
    if (current && canonicalJson(identities(current)) !== canonicalJson(identities(state)))
        throw new Error("備份與目前賽事的選手、場次或場地識別不同，不能覆蓋另一個賽事。");
    const matches = new Set(state.matches.map(match => match.id)), courts = new Set(state.courts.map(court => court.id));
    for (const [matchId, startedAt] of Object.entries(backup.live.starts)) {
        if (!matches.has(matchId) || !Number.isFinite(Date.parse(startedAt))) throw new Error("備份開賽紀錄無效。");
    }
    for (const [matchId, courtId] of Object.entries(backup.live.actual_courts ?? {})) {
        if (!matches.has(matchId) || !courts.has(courtId) || !backup.live.starts[matchId]) throw new Error("備份實際場地紀錄無效。");
    }
    const issues = validate(state);
    if (issues.length) throw new Error("備份賽程未通過檢查。" + issues[0].message);
    const before = new Map(current?.matches.map(match => [match.id, match]) ?? []);
    const changes = state.matches.flatMap(match => {
        const previous = before.get(match.id), after = match;
        return previous && canonicalJson(previous) === canonicalJson(match) ? [] : [{ id: match.id, code: match.code, before: previous ?? null, after }];
    });
    const metadataChanged = !!current && canonicalJson({ ...current, matches: [] }) !== canonicalJson({ ...state, matches: [] });
    const queues=backup.live.queues??{};
    const queued=new Set<string>();
    for(const [court,ids] of Object.entries(queues))for(const id of ids){if(!courts.has(court)||!matches.has(id)||queued.has(id))throw new Error("備份派場資料無效。");queued.add(id);}
    const live = { ...emptyLive(state), queues, starts: backup.live.starts, actual_courts: backup.live.actual_courts ?? {} };
    return { backup, state, live, changes, metadataChanged, completed: state.matches.filter(match => match.status === "completed").length };
}
