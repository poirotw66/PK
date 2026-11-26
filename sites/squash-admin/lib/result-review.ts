import { z } from "zod";
import { database, HttpError } from "./auth";
import type { Tournament } from "./schedule/schema";
import { resultSchema } from "./scoring";
import { reviewResultChange } from "./result-review-model";
import { commitReviewedResult } from "./publication";

const requestSchema = z.object({ revision: z.string().min(1).max(100), sequence: z.number().int().nonnegative(), match_id: z.string().min(1).max(100),
    result: resultSchema, reason: z.string().trim().min(1).max(500), score_version: z.number().int().nonnegative().optional() }).strict();
async function current() {
    const db = database(), main = await db.prepare("SELECT revision,payload FROM tournament WHERE id=1").first<{ revision: string; payload: string }>();
    if (!main) throw new HttpError(404, "尚未匯入賽程。");
    const publication = await db.prepare("SELECT sequence FROM public_state WHERE id=1").first<{ sequence: number }>();
    if (!publication) throw new HttpError(409, "請先發布賽程，再登錄成績。");
    return { revision: main.revision, sequence: publication.sequence, state: JSON.parse(main.payload) as Tournament };
}
async function check(input: unknown) {
    const request = requestSchema.parse(input), value = await current();
    if (request.revision !== value.revision || request.sequence !== value.sequence) throw new HttpError(409, "賽程或成績已更新，請重新整理後再預覽。");
    let result;
    try { result = reviewResultChange(value.state, request.match_id, request.result); }
    catch (error) { throw new HttpError(409, error instanceof Error ? error.message : "成績無法套用。"); }
    const match = value.state.matches.find(row => row.id === request.match_id)!;
    if (match.status === "in_progress") {
        const score = await database().prepare("SELECT version FROM match_scores WHERE match_id=?").bind(match.id).first<{ version: number }>();
        if (request.score_version === undefined || score?.version !== request.score_version) throw new HttpError(409, "比分已更新，請重新選取場次並核對成績。");
    }
    return { request, value, result, match };
}
export async function previewResult(input: unknown, session: string, canCorrect = true) {
    const { request, value, result, match } = await check(input), id = crypto.randomUUID(), now = Date.now(), db = database();
    if (result.correction && !canCorrect) throw new HttpError(403, "只有主管理員可以更正已確認成績。");
    await db.prepare("DELETE FROM previews WHERE created<?").bind(now - 600000).run();
    await db.prepare("INSERT INTO previews(id,session,base,payload,changes,reason,created) VALUES(?,?,?,?,?,?,?)")
        .bind(id, session, value.revision, JSON.stringify({ format: "pk-result-review-v1", request }), JSON.stringify([{ id: match.id, before: result.before, after: result.after }]), request.reason, now).run();
    return { id, correction: result.correction, before: result.before, after: result.after, invalidated: result.invalidated, reason: request.reason, match: { id: match.id, code: match.code, side_a: match.side_a, side_b: match.side_b } };
}
export async function confirmResult(input: unknown, session: string, actor: string, canCorrect = true) {
    const { id } = z.object({ id: z.string().min(1).max(100) }).strict().parse(input), db = database();
    const plan = await db.prepare("SELECT payload FROM previews WHERE id=? AND session=? AND created>?").bind(id, session, Date.now() - 600000).first<{ payload: string }>();
    if (!plan) throw new HttpError(409, "預覽已到期或已使用，請重新核對成績。");
    const saved = JSON.parse(plan.payload);
    if (saved.format !== "pk-result-review-v1") throw new HttpError(409, "請使用成績登錄的專用確認流程。");
    const { request, result: reviewed } = await check(saved.request);
    if (reviewed.correction && !canCorrect) throw new HttpError(403, "只有主管理員可以更正已確認成績。");
    const result = await commitReviewedResult(request.revision, request.sequence, request.match_id, request.result, request.reason, actor, request.score_version);
    await db.prepare("DELETE FROM previews WHERE id=? AND session=?").bind(id, session).run();
    return result;
}
