import { z } from "zod";
import { env } from "cloudflare:workers";
import { database, HttpError } from "./auth";
import { buildBackup, backupSchema, reviewBackup } from "./backup-model";
import { emptyLive, envelope } from "./publication-model";
import type { PublicationPayload } from "./publication-model";
import type { Tournament } from "./schedule/schema";

type Current = { revision: string | null; state: Tournament | null; sequence: number; publication: PublicationPayload | null };
async function current(): Promise<Current> {
    const db = database();
    const [tournament, publication] = await db.batch([
        db.prepare("SELECT revision,payload FROM tournament WHERE id=1"),
        db.prepare("SELECT sequence,payload FROM public_state WHERE id=1"),
    ]);
    const main = tournament.results[0] as { revision: string; payload: string } | undefined;
    const published = publication.results[0] as { sequence: number; payload: string } | undefined;
    return { revision: main?.revision ?? null, state: main ? JSON.parse(main.payload) : null, sequence: published?.sequence ?? 0, publication: published ? JSON.parse(published.payload) : null };
}
function checkIdle(value: Current) {
    if (value.state?.matches.some(match => match.status === "in_progress") || Object.values(value.publication?.live.courts ?? {}).some(court => court.match_id))
        throw new HttpError(409, "仍有比賽進行中，請先確認完賽成績或撤銷開賽，再還原。");
}
export async function exportBackup(origin: string) {
    const value = await current();
    if (!value.state || !value.revision) throw new HttpError(404, "尚未建立可備份的賽程。");
    const history = await database().prepare("SELECT id,parent,actor,reason,created FROM revisions ORDER BY created,id LIMIT 10001").all();
    if (history.results.length > 10000) throw new HttpError(409, "歷程超過單檔備份上限，請使用資料庫平台備份。");
    const backup = await buildBackup({ origin, exported_at: new Date().toISOString(), cloud_revision: value.revision,
        sequence: value.sequence, state: value.state, live: value.publication?.live ?? emptyLive(value.state), history: history.results as Parameters<typeof buildBackup>[0]["history"] });
    if (new TextEncoder().encode(JSON.stringify(backup)).length > 8 * 1024 * 1024) throw new HttpError(413, "備份超過 8 MB，請使用資料庫平台備份。");
    return backup;
}
async function check(input: unknown, origin: string) {
    const value = await current(); checkIdle(value);
    try { return { ...await reviewBackup(input, origin, value.state), value }; }
    catch (error) { if (error instanceof z.ZodError) throw error; throw new HttpError(409, error instanceof Error ? error.message : "備份無效。"); }
}
export async function previewBackup(input: unknown, session: string, origin: string) {
    const request = z.object({ envelope: backupSchema, reason: z.string().trim().min(1).max(500) }).strict().parse(input);
    const result = await check(request.envelope, origin);
    const id = crypto.randomUUID(), now = Date.now(), db = database();
    await db.prepare("DELETE FROM previews WHERE created<?").bind(now - 600000).run();
    await db.prepare("INSERT INTO previews (id,session,base,payload,changes,reason,created) VALUES (?,?,?,?,?,?,?)")
        .bind(id, session, result.value.revision ?? "uninitialized", JSON.stringify({ format: "pk-backup-preview-v1", envelope: result.backup, base_revision: result.value.revision, base_sequence: result.value.sequence }), JSON.stringify(result.changes), request.reason, now).run();
    return { id, changes: result.changes, metadata_changed: result.metadataChanged, completed: result.completed,
        exported_at: result.backup.exported_at, matches: result.state.matches.length, source_revision: result.backup.cloud_revision,
        initialization: !result.value.state, can_apply: true };
}
export async function confirmBackup(input: unknown, session: string, origin: string, actor: string) {
    const { id } = z.object({ id: z.string().min(1).max(100) }).strict().parse(input);
    const db = database();
    const plan = await db.prepare("SELECT payload,reason FROM previews WHERE id=? AND session=? AND created>?")
        .bind(id, session, Date.now() - 600000).first<{ payload: string; reason: string }>();
    if (!plan) throw new HttpError(409, "備份預覽已到期或已使用，請重新檢查。");
    const saved = JSON.parse(plan.payload);
    if (saved.format !== "pk-backup-preview-v1") throw new HttpError(409, "請使用備份還原專用確認流程。");
    const result = await check(saved.envelope, origin);
    if (saved.base_revision !== result.value.revision || saved.base_sequence !== result.value.sequence)
        throw new HttpError(409, "後台在預覽後已有異動，請重新檢查備份，不能覆蓋新資料。");
    if (!env.PUBLISH_SOURCE_ID) throw new HttpError(503, "公開網站連線尚未設定，不能建立還原發布。");
    const revision = crypto.randomUUID(), dispatch = crypto.randomUUID(), created = Date.now(), now = new Date(created).toISOString();
    const sequence = Math.max(result.value.sequence, result.backup.sequence) + 1;
    if (!Number.isSafeInteger(sequence)) throw new HttpError(409, "發布序號超過安全範圍，請檢查備份。");
    const payload = JSON.stringify(result.state), publication = { schedule: result.state, live: result.live };
    const outgoing = JSON.stringify(envelope(publication, { source_id: env.PUBLISH_SOURCE_ID, sequence, schedule_revision: revision,
        published_at: now, live_updated_at: now, created_at: now, operation: { kind: "restore", base_sequence: sequence - 1 } }));
    const guardedWrite = result.value.revision
        ? db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND COALESCE((SELECT sequence FROM public_state WHERE id=1),0)=?").bind(revision, payload, result.value.revision, result.value.sequence)
        : db.prepare("INSERT INTO tournament(id,revision,payload) SELECT 1,?,? WHERE NOT EXISTS(SELECT 1 FROM tournament WHERE id=1) AND COALESCE((SELECT sequence FROM public_state WHERE id=1),0)=?").bind(revision, payload, result.value.sequence);
    const restored = await db.batch([
        guardedWrite,
        db.prepare("INSERT INTO revisions(id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,'[]' FROM tournament WHERE id=1 AND revision=?")
            .bind(revision, result.value.revision, actor, "Backup restore: " + plan.reason, created, payload, revision),
        db.prepare("INSERT INTO public_state(id,sequence,dispatch_id,schedule_revision,payload,published_at,live_updated_at,ack_sequence) SELECT 1,?,?,?,?,?,?,0 FROM tournament WHERE id=1 AND revision=? ON CONFLICT(id) DO UPDATE SET sequence=excluded.sequence,dispatch_id=excluded.dispatch_id,schedule_revision=excluded.schedule_revision,payload=excluded.payload,published_at=excluded.published_at,live_updated_at=excluded.live_updated_at,last_error=NULL")
            .bind(sequence, dispatch, revision, JSON.stringify(publication), now, now, revision),
        db.prepare("INSERT INTO public_outbox(id,sequence,payload,actor,action,created,delivered) SELECT ?,?,?,?,'restore',?,0 FROM tournament WHERE id=1 AND revision=?")
            .bind(dispatch, sequence, outgoing, actor, created, revision),
        db.prepare("DELETE FROM previews WHERE id=? AND EXISTS(SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(id, revision),
    ]);
    if (restored[0].meta.changes !== 1) throw new HttpError(409, "後台已有新異動，請重新檢查備份。");
    return { ok: true, revision, sequence, restored_matches: result.state.matches.length };
}
