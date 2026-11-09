import { retryPublicSync } from "../../../lib/sync-service";
import { waitUntil } from "cloudflare:workers";
import { z } from "zod";
import { HttpError, adminIdentity, checkCsrf, checkOrigin, cookie, createSession, database, findAdmin, identity, readSession } from "../../../lib/auth";
import { readTournament } from "../../../lib/schedule/schema";
import type { Tournament } from "../../../lib/schedule/schema";
import { review, validate } from "../../../lib/schedule/rules";
import { controlState, queuePublication, queueLive, flushPublication } from "../../../lib/publication";

import { flushScores } from "../../../lib/score-publication";
import { readScore, savePoint } from "../../../lib/score-storage";
import { chatGPTSignOutPath } from "../../chatgpt-auth";

export const dynamic = "force-dynamic";
const previewInput = z.object({ revision: z.string().min(1), updates: z.unknown(), reason: z.string().trim().min(1).max(500) }).strict();
const confirmation = z.object({ id: z.string().min(1).max(100) }).strict();
function syncScoreInBackground(matchId: string) {
    waitUntil(flushScores(matchId).catch(() => console.warn("Background score synchronization failed.")));
}
function json(value: unknown, status = 200, headers: Record<string, string> = {}) {
    return Response.json(value, { status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", ...headers } });
}
async function body(request: Request) {
    if (!request.headers.get("Content-Type")?.startsWith("application/json"))
        throw new HttpError(415, "請使用 JSON 資料。");
    const reader = request.body?.getReader();
    if (!reader)
        throw new HttpError(400, "未收到資料。");
    let size = 0;
    const chunks: Uint8Array[] = [];
    for (;;) {
        const { value, done } = await reader.read();
        if (done)
            break;
        size += value.length;
        if (size > 1024 * 1024) {
            await reader.cancel();
            throw new HttpError(413, "資料不得超過 1 MB。");
        }
        chunks.push(value);
    }
    const all = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
        all.set(chunk, offset);
        offset += chunk.length;
    }
    try {
        return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(all));
    }
    catch {
        throw new HttpError(400, "JSON 資料格式無效。");
    }
}
async function current() {
    const row = await database().prepare("SELECT revision,payload FROM tournament WHERE id=1").first<{
        revision: string;
        payload: string;
    }>();
    if (!row)
        throw new HttpError(404, "尚未匯入賽程。");
    return { revision: row.revision, state: JSON.parse(row.payload) as Tournament };
}
async function handle(request: Request) {
    const path = new URL(request.url).pathname.replace(/^\/api\//, "");
    if (path === "sync-service") return json(await retryPublicSync(request));
    const db = database();
    if (request.method === "GET" && path === "status") {
        const user = await identity();
        const account = await findAdmin(user);
        const signIn = { email: user.email, sign_out_url: chatGPTSignOutPath("/") };
        if (!account)
            return json({ error: "此帳號沒有管理權限。", sign_in: signIn }, 403);
        const imported = await db.prepare("SELECT revision FROM tournament WHERE id=1").first();
        const active = await readSession(request, account.owner) ?? await createSession(request, account.owner);
        return json(
            { imported: !!imported, can_import: account.kind === "primary", csrf: active.csrf, sign_in: signIn },
            200, "setCookie" in active ? { "Set-Cookie": active.setCookie } : {},
        );
    }
    if (request.method !== "GET")
        checkOrigin(request);
    const account = await adminIdentity();
    const active = await readSession(request, account.owner);
    if (!active)
        throw new HttpError(401, "操作台工作階段已到期，請重新連線。");
    if (request.method === "GET") {
        if (path === "score") {
            const matchId = z.string().min(1).max(100).parse(new URL(request.url).searchParams.get("match_id"));
            return json(await readScore(matchId));
        }
        if (path === "control") return json(await controlState());
        if (path === "state") {
            const value = await current();
            const history = await db.prepare("SELECT id,parent,actor,reason,created,changes FROM revisions ORDER BY created DESC LIMIT 30").all();
            return json({ ...value, issues: validate(value.state), history: history.results.map(row => ({ ...row, changes: JSON.parse(String(row.changes)) })) });
        }
        throw new HttpError(404, "找不到此頁面。");
    }
    checkCsrf(request, active.csrf);
    if (path === "import") {
        const { owner, kind } = account;
        if (kind !== "primary")
            throw new HttpError(403, "只有原管理員可匯入賽程。");
        if (await db.prepare("SELECT id FROM tournament WHERE id=1").first())
            throw new HttpError(409, "賽程已匯入；此版本不提供覆蓋匯入。");
        const state = readTournament(await body(request)), issues = validate(state);
        if (issues.length)
            throw new HttpError(400, `賽程有 ${issues.length} 項衝突，請先在本機修正。${issues[0].message}`);
        const revision = crypto.randomUUID(), payload = JSON.stringify(state), now = Date.now();
        await db.batch([
            db.prepare("INSERT INTO administrator (id,owner) VALUES (1,?) ON CONFLICT(id) DO NOTHING").bind(owner),
            db.prepare("INSERT INTO tournament (id,revision,payload) VALUES (1,?,?)").bind(revision, payload),
            db.prepare("INSERT INTO revisions (id,parent,actor,reason,created,payload,changes) VALUES (?,NULL,?,'Initial import',?,?, '[]')").bind(revision, owner, now, payload),
        ]);
        return json({ ok: true, revision, matches: state.matches.length });
    }
    if (path === "score-sync") {
        const input = z.object({match_id:z.string().min(1).max(100)}).strict().parse(await body(request));
        syncScoreInBackground(input.match_id);
        return json({ok:true});
    }
    if (path === "score") {
        const input = z.object({ match_id:z.string().min(1).max(100), version:z.number().int().nonnegative(), action:z.enum(["a","b","undo"]) }).strict().parse(await body(request));
        const saved = await savePoint(input.match_id, input.version, input.action, account.actor);
        syncScoreInBackground(input.match_id);
        return json({...saved, sync_pending:true, sync_error:null});
    }
    if (path === "publish") {
        const input=z.object({revision:z.string().min(1),sequence:z.number().int().nonnegative()}).strict().parse(await body(request));
        await queuePublication(input.revision,input.sequence,account.actor);
        await flushPublication();
        await flushScores();
        return json(await controlState());
    }
    if (path === "live") {
        const input=z.object({revision:z.string().min(1),sequence:z.number().int().nonnegative(),action:z.enum(["start","end","cancel"]),court_id:z.string().min(1),match_id:z.string().min(1),reason:z.string().trim().max(300).optional(),score_version:z.number().int().nonnegative().optional()}).strict().parse(await body(request));
        if(input.action==="cancel"&&!input.reason)throw new HttpError(400,"撤銷開賽需要填寫原因。");
        await queueLive(input,account.actor);
        await flushPublication();
        await flushScores();
        return json(await controlState());
    }
    if (path === "sync-retry") {
        await flushPublication();
        await flushScores();
        return json(await controlState());
    }
    if (path === "logout") {
        await db.prepare("DELETE FROM sessions WHERE id=?").bind(active.id).run();
        return json({ ok: true }, 200, { "Set-Cookie": cookie(request, "", true) });
    }
    if (path === "preview") {
        const input = previewInput.parse(await body(request)), value = await current();
        if (input.revision !== value.revision)
            throw new HttpError(409, "賽程已被更新，請重新整理後檢查草案。");
        const result = review(value.state, input.updates), id = crypto.randomUUID(), now = Date.now();
        if (result.can_apply) {
            await db.prepare("DELETE FROM previews WHERE created<?").bind(now - 600000).run();
            await db.prepare("INSERT INTO previews (id,session,base,payload,changes,reason,created) VALUES (?,?,?,?,?,?,?)").bind(id, active.id, value.revision, JSON.stringify(result.after), JSON.stringify(result.changes), input.reason, now).run();
        }
        return json({ id: result.can_apply ? id : null, changes: result.changes, introduced: result.introduced, remaining: result.remaining, can_apply: result.can_apply });
    }
    if (path === "confirm") {
        const { id } = confirmation.parse(await body(request));
        const plan = await db.prepare("SELECT * FROM previews WHERE id=? AND session=? AND created>?").bind(id, active.id, Date.now() - 600000).first<{
            base: string;
            payload: string;
            changes: string;
            reason: string;
        }>();
        if (!plan)
            throw new HttpError(409, "預覽已到期或已使用，請重新檢查草案。");
        const value = await current();
        if (value.revision !== plan.base)
            throw new HttpError(409, "有人已更新賽程，請重新整理後再檢查。");
        const candidate = readTournament(JSON.parse(plan.payload));
        if (validate(candidate).length)
            throw new HttpError(409, "草案不符合賽程限制，無法儲存。");
        const revision = crypto.randomUUID(), now = Date.now();
        const results = await db.batch([
            db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=?").bind(revision, plan.payload, plan.base),
            db.prepare("INSERT INTO revisions (id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?").bind(revision, plan.base, account.actor, plan.reason, now, plan.payload, plan.changes, revision),
            db.prepare("DELETE FROM previews WHERE id=? AND EXISTS (SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(id, revision),
        ]);
        if (results[0].meta.changes !== 1)
            throw new HttpError(409, "有人已更新賽程，請重新整理後再檢查。");
        return json({ ok: true, revision });
    }
    throw new HttpError(404, "找不到此操作。");
}
async function boundary(request: Request) {
    try {
        return await handle(request);
    }
    catch (error) {
        if (error instanceof HttpError)
            return json({ error: error.message }, error.status);
        if (error instanceof z.ZodError)
            return json({ error: "資料格式或必填欄位無效。" }, 400);
        return json({ error: "目前無法完成操作，請稍後再試。" }, 500);
    }
}
export const GET = boundary;
export const POST = boundary;
