import { z } from "zod";
import { database, HttpError } from "./auth";
import { reviewLocalUpdate, localUpdateSchema } from "./local-update-model";
import type { Tournament } from "./schedule/schema";

async function current() {
    const db = database();
    const [canonical, publication] = await db.batch([
        db.prepare("SELECT revision,payload FROM tournament WHERE id=1"),
        db.prepare("SELECT sequence,payload FROM public_state WHERE id=1"),
    ]);
    const main = canonical.results[0] as { revision: string; payload: string } | undefined;
    if (!main) throw new HttpError(404, "尚未匯入賽程。");
    const published = publication.results[0] as { sequence: number; payload: string } | undefined;
    if (published && Object.values((JSON.parse(published.payload) as { live: { courts: Record<string, { match_id: string | null }> } }).live.courts).some(court => court.match_id))
        throw new HttpError(409, "仍有場地正在比賽，不能匯入地端重排。");
    return { revision: main.revision, state: JSON.parse(main.payload) as Tournament, sequence: published?.sequence ?? 0 };
}

async function check(input: unknown, origin: string) {
    const value = await current();
    try {
        const result = await reviewLocalUpdate(value.state, input, origin);
        if (result.update.base_cloud_revision !== value.revision || result.update.base_sequence !== value.sequence)
            throw new Error("雲端已更新，請重新下載交接檔並在地端核對，不能覆蓋現場異動。");
        return { ...result, value };
    } catch (error) {
        if (error instanceof z.ZodError) throw error;
        throw new HttpError(409, error instanceof Error ? error.message : "地端交接檔無效。");
    }
}

export async function previewLocalUpdate(input: unknown, session: string, origin: string) {
    const request = z.object({ envelope: localUpdateSchema, reason: z.string().trim().min(1).max(500) }).strict().parse(input);
    const result = await check(request.envelope, origin);
    const id = crypto.randomUUID(), now = Date.now(), db = database();
    await db.prepare("DELETE FROM previews WHERE created<?").bind(now - 600000).run();
    await db.prepare("INSERT INTO previews (id,session,base,payload,changes,reason,created) VALUES (?,?,?,?,?,?,?)")
        .bind(id, session, result.value.revision, JSON.stringify({ format: "pk-local-update-preview-v1", envelope: result.update }), JSON.stringify(result.changes), request.reason, now).run();
    return { id, can_apply: true, changes: result.changes, policy_changes: result.policy_changes, introduced: [], remaining: [], local_update: true };
}

export async function confirmLocalUpdate(input: unknown, session: string, origin: string, actor: string) {
    const { id } = z.object({ id: z.string().min(1).max(100) }).strict().parse(input);
    const db = database();
    const plan = await db.prepare("SELECT payload,changes,reason FROM previews WHERE id=? AND session=? AND created>?")
        .bind(id, session, Date.now() - 600000).first<{ payload: string; changes: string; reason: string }>();
    if (!plan) throw new HttpError(409, "預覽已到期或已使用，請重新檢查地端交接檔。");
    const saved = JSON.parse(plan.payload);
    if (saved.format !== "pk-local-update-preview-v1") throw new HttpError(409, "請使用地端交接檔的專用確認流程。");
    const result = await check(saved.envelope, origin);
    const revision = crypto.randomUUID(), payload = JSON.stringify(result.state), now = Date.now();
    const updated = await db.batch([
        db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND COALESCE((SELECT sequence FROM public_state WHERE id=1),0)=?")
            .bind(revision, payload, result.value.revision, result.value.sequence),
        db.prepare("INSERT INTO revisions (id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?")
            .bind(revision, result.value.revision, actor, "Local replan: " + plan.reason, now, payload, plan.changes, revision),
        db.prepare("DELETE FROM previews WHERE id=? AND EXISTS (SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(id, revision),
    ]);
    if (updated[0].meta.changes !== 1) throw new HttpError(409, "雲端已更新，請重新檢查地端交接檔。");
    return { ok: true, revision };
}
