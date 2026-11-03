import { z } from "zod";
import { HttpError, admin, checkOrigin, cookie, createSession, database, identity, ownerIdentity, passwordHash, randomToken, session, verifyPassword } from "../../../lib/auth";
import { readTournament } from "../../../lib/schedule/schema";
import type { Tournament } from "../../../lib/schedule/schema";
import { review, validate } from "../../../lib/schedule/rules";
import { controlState, queuePublication, queueLive, flushPublication } from "../../../lib/publication";

export const dynamic = "force-dynamic";
const credentials = z.object({ username: z.string().regex(/^[A-Za-z0-9_.-]{3,64}$/), password: z.string().min(12).max(256) }).strict();
const previewInput = z.object({ revision: z.string().min(1), updates: z.unknown(), reason: z.string().trim().min(1).max(500) }).strict();
const confirmation = z.object({ id: z.string().min(1).max(100) }).strict();
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
    const db = database();
    if (request.method === "GET") {
        if (path === "status") {
            const { record } = await ownerIdentity();
            let active = null;
            try {
                active = await session(request);
            }
            catch (error) {
                if (!(error instanceof HttpError) || error.status !== 401)
                    throw error;
            }
            const imported = await db.prepare("SELECT revision FROM tournament WHERE id=1").first();
            return json({ configured: !!record?.password_hash, imported: !!imported, authenticated: !!active, csrf: active?.csrf, username: active?.username, platform_private: true });
        }
        await session(request);
        if (path === "control") return json(await controlState());
        if (path === "state") {
            const value = await current();
            const history = await db.prepare("SELECT id,parent,actor,reason,created,changes FROM revisions ORDER BY created DESC LIMIT 30").all();
            return json({ ...value, issues: validate(value.state), history: history.results.map(row => ({ ...row, changes: JSON.parse(String(row.changes)) })) });
        }
        throw new HttpError(404, "找不到此頁面。");
    }
    checkOrigin(request);
    if (path === "setup") {
        const { owner, record } = await ownerIdentity();
        if (record?.password_hash)
            throw new HttpError(409, "管理員已設定，請直接登入。");
        const { username, password } = credentials.parse(await body(request));
        const salt = randomToken(), hashed = salt + ":" + await passwordHash(password, salt);
        const result = await db.prepare("INSERT INTO administrator (id,owner,username,password_hash) VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash WHERE administrator.owner=excluded.owner AND administrator.password_hash IS NULL").bind(owner, username, hashed).run();
        if (result.meta.changes !== 1)
            throw new HttpError(409, "管理員已設定，請重新整理。");
        return json({ ok: true }, 200, { "Set-Cookie": await createSession(request, owner) });
    }
    if (path === "login") {
        const owner = await identity();
        const input = z.object({ username: z.string().max(64), password: z.string().max(256) }).strict().parse(await body(request));
        const now = Date.now();
        const attempt = await db.prepare("INSERT INTO login_attempts (owner,window,count) VALUES (?,?,1) ON CONFLICT(owner) DO UPDATE SET count=CASE WHEN login_attempts.window<? THEN 1 ELSE login_attempts.count+1 END,window=CASE WHEN login_attempts.window<? THEN excluded.window ELSE login_attempts.window END RETURNING count").bind(owner, now, now - 60000, now - 60000).first<{
            count: number;
        }>();
        if (!attempt || attempt.count > 5)
            throw new HttpError(429, "登入嘗試次數過多，請一分鐘後再試。");
        const record = await admin(), valid = await verifyPassword(input.username, input.password, record);
        if (!valid || record?.owner !== owner)
            throw new HttpError(401, "帳號或密碼不正確。");
        return json({ ok: true }, 200, { "Set-Cookie": await createSession(request, owner) });
    }
    if (path === "import") {
        const { owner, record } = await ownerIdentity();
        if (record?.password_hash)
            await session(request, true);
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
    const active = await session(request, true);
    if (path === "publish") {
        const input=z.object({revision:z.string().min(1),sequence:z.number().int().nonnegative()}).strict().parse(await body(request));
        await queuePublication(input.revision,input.sequence,active.username);
        await flushPublication();
        return json(await controlState());
    }
    if (path === "live") {
        const input=z.object({revision:z.string().min(1),sequence:z.number().int().nonnegative(),action:z.enum(["start","end","cancel"]),court_id:z.string().min(1),match_id:z.string().min(1),reason:z.string().trim().max(300).optional()}).strict().parse(await body(request));
        if(input.action==="cancel"&&!input.reason)throw new HttpError(400,"撤銷開賽需要填寫原因。");
        await queueLive(input,active.username);
        await flushPublication();
        return json(await controlState());
    }
    if (path === "sync-retry") {
        await flushPublication();
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
            db.prepare("INSERT INTO revisions (id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,? FROM tournament WHERE id=1 AND revision=?").bind(revision, plan.base, active.username, plan.reason, now, plan.payload, plan.changes, revision),
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
