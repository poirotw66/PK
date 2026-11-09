import { env } from "cloudflare:workers";
import { database, HttpError } from "./auth";
import { flushPublication, publicationRow } from "./publication";
import { flushScores } from "./score-publication";
import { validSyncServiceToken } from "./sync-service-auth";
async function syncSummary() {
    const row = await publicationRow();
    const scores = await database().prepare("SELECT COUNT(*) AS count FROM match_scores s,tournament t,json_each(t.payload,'$.matches') m WHERE t.id=1 AND s.synced_version<s.version AND s.match_id=json_extract(m.value,'$.id') AND json_extract(m.value,'$.status')='in_progress'").first<{count:number}>();
    return {publication_pending:!!row && row.ack_sequence < row.sequence, scores_pending:scores?.count ?? 0, sequence:row?.sequence ?? 0, acknowledged_sequence:row?.ack_sequence ?? 0, public_version:row?.ack_version ?? null};
}
export async function retryPublicSync(request: Request) {
    if (request.method !== "POST") throw new HttpError(405,"Method not allowed");
    if (!env.SYNC_RETRY_TOKEN_HASH) throw new HttpError(503,"Cloud retry is not configured");
    if (!await validSyncServiceToken(request.headers.get("Authorization"),env.SYNC_RETRY_TOKEN_HASH)) throw new HttpError(401,"Unauthorized updater");
    // No request data is accepted: this can only resend already-approved durable messages.
    if (request.body) {
        const reader = request.body.getReader();
        const chunk = await reader.read();
        await reader.cancel();
        if (!chunk.done) throw new HttpError(400,"Updater does not accept a body");
    }
    const before = await syncSummary();
    await flushPublication();
    await flushScores();
    const after = await syncSummary();
    return {checked_at:new Date().toISOString(), before, after, pending:after.publication_pending || after.scores_pending > 0};
}
