import { z } from "zod";
import { database, HttpError } from "./auth";
import { readTournament } from "./schedule/schema";
import { advancementInput, reviewAdvancement } from "./advancement-model";

async function current() {
    const result = await database().batch([
        database().prepare("SELECT revision,payload FROM tournament WHERE id=1"),
        database().prepare("SELECT sequence FROM public_state WHERE id=1"),
    ]);
    const row = result[0].results[0] as { revision: string; payload: string } | undefined;
    if (!row) throw new HttpError(404, "尚未匯入賽程。");
    return { revision: row.revision, state: readTournament(JSON.parse(row.payload)), sequence: Number((result[1].results[0] as { sequence?: number } | undefined)?.sequence ?? 0) };
}
function review(input: Parameters<typeof reviewAdvancement>[0], orderings: Parameters<typeof reviewAdvancement>[1], source: Parameters<typeof reviewAdvancement>[2]) {
    try { return reviewAdvancement(input, orderings, source); }
    catch (error) {
        if (error instanceof z.ZodError) throw error;
        throw new HttpError(409, error instanceof Error ? error.message : "晉級資料無法核對。");
    }
}
export async function previewAdvancement(input: unknown, session: string) {
    const request = advancementInput.parse(input), value = await current();
    if (request.revision !== value.revision || request.sequence !== value.sequence) throw new HttpError(409, "賽程或成績已更新，請重新整理後核對晉級。");
    const result = review(value.state, request.pool_orderings, request.source_state), id = result.can_apply ? crypto.randomUUID() : null;
    if (id) await database().prepare("INSERT INTO previews (id,session,base,payload,changes,reason,created) VALUES (?,?,?,?,?,?,?)")
        .bind(id, session, value.revision, JSON.stringify({ format: "pk-advancement-preview-v1", request }), JSON.stringify(result.changes), request.reason, Date.now()).run();
    return { id, can_apply: result.can_apply, changes: result.changes, pending: result.pending, blockers: result.blockers, issues: result.issues, decision_changed: result.decision_changed, source_attached: result.source_attached };
}
export async function confirmAdvancement(input: unknown, session: string, actor: string) {
    const { id } = z.object({ id: z.string().min(1).max(100) }).strict().parse(input);
    const db = database(), plan = await db.prepare("SELECT payload,reason FROM previews WHERE id=? AND session=? AND created>?").bind(id, session, Date.now() - 600000).first<{ payload: string; reason: string }>();
    if (!plan) throw new HttpError(409, "晉級預覽已到期或已使用，請重新核對。");
    const saved = JSON.parse(plan.payload);
    if (saved.format !== "pk-advancement-preview-v1") throw new HttpError(409, "請使用晉級核對的專用確認流程。");
    const request = advancementInput.parse(saved.request), value = await current();
    if (request.revision !== value.revision || request.sequence !== value.sequence) throw new HttpError(409, "賽程或成績已更新，請重新核對晉級。");
    const result = review(value.state, request.pool_orderings, request.source_state);
    if (!result.can_apply) throw new HttpError(409, "晉級來源或賽程條件已改變，不能套用。");
    const revision = crypto.randomUUID(), payload = JSON.stringify(result.state), changes = JSON.stringify(result.changes);
    const updated = await db.batch([
        db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND COALESCE((SELECT sequence FROM public_state WHERE id=1),0)=? AND EXISTS (SELECT 1 FROM previews WHERE id=? AND session=? AND created>?)").bind(revision, payload, value.revision, value.sequence, id, session, Date.now() - 600000),
        db.prepare("INSERT INTO revisions (id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?").bind(revision, value.revision, actor, "Advancement confirmation: " + plan.reason, Date.now(), payload, changes, revision),
        db.prepare("DELETE FROM previews WHERE id=? AND EXISTS (SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(id, revision),
    ]);
    if (updated[0].meta.changes !== 1) throw new HttpError(409, "其他操作已更新資料，請重新核對晉級。");
    return { ok: true, revision, requires_publication: true };
}
