import { env } from "cloudflare:workers";
import { z } from "zod";
import { database, HttpError } from "./auth";
import { validate } from "./schedule/rules";
import { readTournament } from "./schedule/schema";

const cutoverConfig = z.object({
    sourceRevision: z.string().min(1).max(100),
    sha256: z.string().regex(/^[a-f0-9]{64}$/),
});
function configuredDraft() {
    return cutoverConfig.safeParse({
        sourceRevision: env.DRAFT_CUTOVER_SOURCE_REVISION,
        sha256: env.DRAFT_CUTOVER_SHA256,
    });
}
const inputSchema = z.object({
    base_revision: z.string().min(1).max(100),
    payload: z.string().max(512 * 1024),
}).strict();

export function canReplaceDraft(row: { matches: number } | null) {
    return row?.matches === 279 && configuredDraft().success;
}

export async function replaceDraftSchedule(input: unknown, actor: string) {
    const { base_revision, payload } = inputSchema.parse(input);
    const config = configuredDraft();
    if (!config.success)
        throw new HttpError(409, "目前未設定暫定賽程切換。");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
    const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    if (hex !== config.data.sha256)
        throw new HttpError(400, "此檔案不是公開站使用的 226 場暫定賽程。");

    const next = readTournament(JSON.parse(payload));
    if (next.source_revision !== config.data.sourceRevision || next.matches.length !== 226
        || next.matches.some(match => match.status !== "planned" || match.result)
        || validate(next).length)
        throw new HttpError(400, "226 場暫定賽程未通過檢查。");

    const db = database(), revision = crypto.randomUUID();
    const active = "EXISTS (SELECT 1 FROM tournament WHERE id=1 AND revision=?)";
    const eligible = active + " AND EXISTS (SELECT 1 FROM tournament WHERE id=1 AND json_array_length(payload,'$.matches')=279)";
    const results = await db.batch([
        db.prepare(`INSERT INTO schedule_cutover_archive (replacement, kind, data)
            SELECT ?, 'tournament', json_object('revision', revision, 'payload', payload) FROM tournament
            WHERE id=1 AND ${eligible}`).bind(revision, base_revision),
        db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND json_array_length(payload,'$.matches')=279")
            .bind(revision, payload, base_revision),
        db.prepare(`INSERT INTO schedule_cutover_archive (replacement, kind, data)
            SELECT ?, 'score', json_object('match_id', match_id, 'version', version, 'payload', payload, 'updated_at', updated_at,
                'actor', actor, 'synced_version', synced_version, 'sync_error', sync_error)
            FROM match_scores WHERE ${active}`).bind(revision, revision),
        db.prepare(`INSERT INTO schedule_cutover_archive (replacement, kind, data)
            SELECT ?, 'publication', json_object('sequence', sequence, 'dispatch_id', dispatch_id, 'schedule_revision', schedule_revision,
                'payload', payload, 'published_at', published_at, 'live_updated_at', live_updated_at, 'ack_sequence', ack_sequence,
                'ack_version', ack_version, 'synced_at', synced_at, 'last_error', last_error)
            FROM public_state WHERE ${active}`).bind(revision, revision),
        db.prepare(`INSERT INTO schedule_cutover_archive (replacement, kind, data)
            SELECT ?, 'outbox', json_object('id', id, 'sequence', sequence, 'payload', payload, 'actor', actor, 'action', action,
                'created', created, 'delivered', delivered)
            FROM public_outbox WHERE ${active}`).bind(revision, revision),
        db.prepare(`DELETE FROM match_scores WHERE ${active}`).bind(revision),
        db.prepare(`DELETE FROM public_outbox WHERE ${active}`).bind(revision),
        db.prepare(`DELETE FROM public_state WHERE ${active}`).bind(revision),
        db.prepare(`INSERT INTO revisions (id,parent,actor,reason,created,payload,changes)
            SELECT ?, ?, ?, 'Switch to the 226-match draft', ?, ?, '[]' WHERE ${active}`)
            .bind(revision, base_revision, actor, Date.now(), payload, revision),
    ]);
    if (results[1].meta.changes !== 1)
        throw new HttpError(409, "賽程已切換或有其他更新，請重新整理。");
    return { ok: true, revision, matches: next.matches.length };
}
