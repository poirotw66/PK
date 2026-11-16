import { env } from "cloudflare:workers";
import { database, HttpError } from "./auth";
import type { Tournament } from "./schedule/schema";
import { validate } from "./schedule/rules";
import { emptyLive, envelope, transitionLive, selectableMatches } from "./publication-model";
import { defaultScoringRules, finishedResult } from "./scoring";
import { readScore } from "./score-storage";
import { publicationDiff } from "./publication-diff";
import type { PublicEnvelope } from "./publication-model";
import type { PublicationPayload } from "./publication-model";
type Row = {
    sequence: number;
    dispatch_id: string;
    schedule_revision: string;
    payload: string;
    published_at: string;
    live_updated_at: string | null;
    ack_sequence: number;
    ack_version: string | null;
    synced_at: string | null;
    last_error: string | null;
};
export async function publicationRow() { return database().prepare("SELECT * FROM public_state WHERE id=1").first<Row>(); }
async function canonical() {
    const row = await database().prepare("SELECT revision,payload FROM tournament WHERE id=1").first<{
        revision: string;
        payload: string;
    }>();
    if (!row)
        throw new HttpError(404, "尚未匯入賽程。");
    return { revision: row.revision, state: JSON.parse(row.payload) as Tournament };
}
function sourceId() { if (!env.PUBLISH_SOURCE_ID)
    throw new HttpError(503, "公開網站連線尚未設定。"); return env.PUBLISH_SOURCE_ID; }
function dispatchPayload(publication: PublicationPayload, row: {
    sequence: number;
    schedule_revision: string;
    published_at: string;
    live_updated_at: string | null;
}, now: string) { return JSON.stringify(envelope(publication, { source_id: sourceId(), ...row, created_at: now })); }
export async function controlState() {
    const main = await canonical(), row = await publicationRow();
    const pendingScores = await database().prepare("SELECT s.sync_error FROM match_scores s,tournament t,json_each(t.payload,'$.matches') m WHERE t.id=1 AND s.synced_version<s.version AND s.match_id=json_extract(m.value,'$.id') AND json_extract(m.value,'$.status')='in_progress' LIMIT 1").first<{sync_error:string|null}>();
    const published = row ? JSON.parse(row.payload) as PublicationPayload : null;
    const acknowledged = row?.ack_sequence ? await database().prepare("SELECT payload FROM public_outbox WHERE sequence=? AND delivered=1").bind(row.ack_sequence).first<{payload:string}>() : null;
    const publication_review = publicationDiff(acknowledged ? JSON.parse(acknowledged.payload) as PublicEnvelope : null, main.state);
    return { dates: main.state.dates, publication_review, revision: main.revision, sequence: row?.sequence ?? 0, published_revision: row?.schedule_revision ?? null,
        has_unpublished: !row || JSON.stringify(published?.schedule.matches.map(m => ({ id: m.id, assignment: m.assignment, status: m.status }))) !== JSON.stringify(main.state.matches.map(m => ({ id: m.id, assignment: m.assignment, status: m.status }))),
        published_at: row?.published_at ?? null, live_updated_at: row?.live_updated_at ?? null, synced_at: row?.synced_at ?? null,
        pending: !!pendingScores || !!row && row.ack_sequence < row.sequence, error: row?.last_error ?? pendingScores?.sync_error ?? null, public_url: env.READER_ORIGIN ?? null,
        courts: main.state.courts.map(court => {
            const live = published?.live.courts[court.id];
            const current = published?.schedule.matches.find(match => match.id === live?.match_id);
            const describe = (match: typeof current) => match ? { id: match.id, code: match.code, division: match.division, side_a: match.side_a, side_b: match.side_b,
                planned_court: main.state.courts.find(c=>c.id===match.assignment?.court)?.name??null,
                result: match.result ?? null, scoring_rules: match.scoring_rules ?? defaultScoringRules,
                planned_start: match.assignment ? new Date(match.assignment.start * 60000).toISOString() : null } : null;
            return { id: court.id, name: court.name, current: current ? { ...describe(current)!, started_at: live?.started_at } : null,
                queue: selectableMatches(main.state, published, court.id).map(({match, eligible, blocked_reason}) => ({ ...describe(match)!, eligible, blocked_reason })) };
        }) };
}
export async function queuePublication(revision: string, sequence: number, actor: string) {
    const db = database(), main = await canonical(), prior = await publicationRow();
    if (main.revision !== revision || (prior?.sequence ?? 0) !== sequence)
        throw new HttpError(409, "賽程或發布狀態已更新，請重新整理後再發布。");
    const issues = validate(main.state);
    if (issues.length)
        throw new HttpError(409, "賽程仍有衝突，無法發布。" + issues[0].message);
    const now = new Date().toISOString(), id = crypto.randomUUID(), next = sequence + 1;
    const previous = prior ? JSON.parse(prior.payload) as PublicationPayload : null;
    const publication: PublicationPayload = { schedule: main.state, live: previous?.live ?? emptyLive(main.state) };
    const payload = JSON.stringify(publication), outgoing = dispatchPayload(publication, { sequence: next, schedule_revision: revision, published_at: now, live_updated_at: prior?.live_updated_at ?? null }, now);
    const results = await db.batch([
        db.prepare("INSERT INTO public_state (id,sequence,dispatch_id,schedule_revision,payload,published_at,live_updated_at,ack_sequence) SELECT 1,?,?,?,?,?,?,0 FROM tournament WHERE id=1 AND revision=? ON CONFLICT(id) DO UPDATE SET sequence=excluded.sequence,dispatch_id=excluded.dispatch_id,schedule_revision=excluded.schedule_revision,payload=excluded.payload,published_at=excluded.published_at,last_error=NULL WHERE public_state.sequence=?").bind(next, id, revision, payload, now, prior?.live_updated_at ?? null, revision, sequence),
        db.prepare("INSERT INTO public_outbox (id,sequence,payload,actor,action,created,delivered) SELECT ?,?,?,?,'publish',?,0 FROM public_state WHERE id=1 AND dispatch_id=?").bind(id, next, outgoing, actor, Date.now(), id),
    ]);
    if (results[0].meta.changes !== 1)
        throw new HttpError(409, "發布狀態已改變，請重新整理。");
}
export async function queueLive(input: {
    revision: string;
    sequence: number;
    action: "start" | "end" | "cancel";
    court_id: string;
    match_id: string;
    reason?: string;
    score_version?: number;
}, actor: string) {
    const db = database(), main = await canonical(), prior = await publicationRow();
    if (!prior)
        throw new HttpError(409, "請先發布一版賽程，才能操作現場賽況。");
    if (main.revision !== input.revision || prior.sequence !== input.sequence)
        throw new HttpError(409, "場地或賽程已更新，請重新整理。");
    let result;
    if (input.action === "end") {
        if (input.score_version === undefined) throw new HttpError(400, "請先進入計分模式並確認成績。");
        const score = await readScore(input.match_id);
        if (score.version !== input.score_version) throw new HttpError(409, "比分已更新，請重新確認成績。");
        try { result = finishedResult(score.score); }
        catch (error) { throw new HttpError(409, (error as Error).message); }
    }
    let changed;
    try {
        changed = transitionLive(main.state, JSON.parse(prior.payload), { ...input, result }, new Date().toISOString());
    }
    catch (error) {
        throw new HttpError(409, (error as Error).message);
    }
    const now = new Date().toISOString(), id = crypto.randomUUID(), revision = crypto.randomUUID(), next = prior.sequence + 1;
    const payload = JSON.stringify(changed.publication), canonicalPayload = JSON.stringify(changed.main);
    const outgoing = dispatchPayload(changed.publication, { sequence: next, schedule_revision: prior.schedule_revision, published_at: prior.published_at, live_updated_at: now }, now);
    const scoreGuard = input.action === "end" ? " AND EXISTS (SELECT 1 FROM match_scores WHERE match_id=? AND version=?)" : "";
    const mutation = db.prepare("UPDATE tournament SET revision=?,payload=? WHERE id=1 AND revision=? AND EXISTS (SELECT 1 FROM public_state WHERE id=1 AND sequence=?)" + scoreGuard);
    const parameters = [revision, canonicalPayload, input.revision, input.sequence];
    const results = await db.batch([
        mutation.bind(...parameters, ...(input.action === "end" ? [input.match_id, input.score_version!] : [])),
        db.prepare("UPDATE public_state SET sequence=?,dispatch_id=?,payload=?,live_updated_at=?,last_error=NULL WHERE id=1 AND sequence=? AND EXISTS (SELECT 1 FROM tournament WHERE id=1 AND revision=?)").bind(next, id, payload, now, input.sequence, revision),
        ...(input.action === "start" ? [db.prepare("INSERT INTO match_scores (match_id,version,payload,updated_at,actor) SELECT ?,1,?,?,? FROM public_state WHERE id=1 AND dispatch_id=? ON CONFLICT(match_id) DO UPDATE SET version=match_scores.version+1,payload=excluded.payload,updated_at=excluded.updated_at,actor=excluded.actor").bind(input.match_id, JSON.stringify({rules:changed.main.matches.find(m=>m.id===input.match_id)?.scoring_rules ?? defaultScoringRules,points:[]}), now, actor, id)] : []),
        db.prepare("INSERT INTO revisions (id,parent,actor,reason,created,payload,changes) SELECT ?,?,?,?,?,?,'[]' FROM public_state WHERE id=1 AND dispatch_id=?").bind(revision, input.revision, actor, `Live ${input.action}: ${input.court_id} / ${input.match_id}${input.reason ? ' / ' + input.reason : ''}`, Date.now(), canonicalPayload, id),
        db.prepare("INSERT INTO public_outbox (id,sequence,payload,actor,action,created,delivered) SELECT ?,?,?,?,?,?,0 FROM public_state WHERE id=1 AND dispatch_id=?").bind(id, next, outgoing, actor, input.action, Date.now(), id),
    ]);
    if (results[0].meta.changes !== 1)
        throw new HttpError(409, "另一個操作已更新場地或比分，請重新整理。");

}
export async function flushPublication() {
    const db = database(), row = await publicationRow();
    if (!row || row.ack_sequence >= row.sequence)
        return;
    const pending = await db.prepare("SELECT payload FROM public_outbox WHERE id=?").bind(row.dispatch_id).first<{
        payload: string;
    }>();
    if (!pending)
        return;
    try {
        if (!env.CLOUD_PUBLISH_SECRET || !env.READER_ORIGIN)
            throw new Error("Missing publication configuration");
        const origin = new URL(env.READER_ORIGIN);
        if (origin.protocol !== "https:" && !(import.meta.env.DEV && ["127.0.0.1", "localhost"].includes(origin.hostname)))
            throw new Error("Invalid reader origin");
        const path = "/sync/cloud-publish", timestamp = String(Math.floor(Date.now() / 1000));
        const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.CLOUD_PUBLISH_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
        const signature = Array.from(new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(timestamp + "\n" + path + "\n" + pending.payload))), byte => byte.toString(16).padStart(2, "0")).join("");
        const response = await fetch(new URL(path, origin), { method: "POST", redirect: "manual", signal: AbortSignal.timeout(12000), headers: { "Content-Type": "application/json", "X-Sync-Time": timestamp, "X-Sync-Signature": signature }, body: pending.payload });
        const result = await response.json() as {
            source_id?: string;
            sequence?: number;
            version?: string;
            error?: string;
        };
        if ((!response.ok && response.status !== 409) || result.source_id !== env.PUBLISH_SOURCE_ID || !Number.isSafeInteger(result.sequence) || result.sequence! < row.sequence || !/^[a-f0-9]{64}$/.test(result.version ?? ""))
            throw new Error("Reader did not acknowledge publication");
        const now = new Date().toISOString();
        await db.batch([
            db.prepare("UPDATE public_state SET ack_sequence=?,ack_version=?,synced_at=?,last_error=NULL WHERE id=1 AND dispatch_id=? AND ack_sequence<?").bind(result.sequence, result.version, now, row.dispatch_id, result.sequence),
            db.prepare("UPDATE public_outbox SET delivered=1 WHERE sequence<=? AND EXISTS (SELECT 1 FROM public_state WHERE id=1 AND dispatch_id=?)").bind(result.sequence, row.dispatch_id),
        ]);
    }
    catch {
        await db.prepare("UPDATE public_state SET last_error=? WHERE id=1 AND dispatch_id=? AND ack_sequence<sequence").bind("公開網站暫時未收到更新，請重試同步；後台資料已保存。", row.dispatch_id).run();
    }
}
