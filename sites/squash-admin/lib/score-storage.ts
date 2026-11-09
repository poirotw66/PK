import { database, HttpError } from "./auth";
import { defaultScoringRules, scorePoint, scoreSchema } from "./scoring";
import type { Score, Side } from "./scoring";
import type { Tournament } from "./schedule/schema";
export async function readScore(matchId: string) {
    const db = database(), main = await db.prepare("SELECT payload FROM tournament WHERE id=1").first<{payload:string}>();
    const match = main && (JSON.parse(main.payload) as Tournament).matches.find(row => row.id === matchId);
    if (!match || match.status !== "in_progress") throw new HttpError(409, "此場比賽已結束或尚未開賽，請返回場地列表。");
    const row = await db.prepare("SELECT version,payload,updated_at,synced_version,sync_error FROM match_scores WHERE match_id=?").bind(matchId).first<{version:number;payload:string;updated_at:string;synced_version:number;sync_error:string|null}>();
    return { sync_pending: !row || row.synced_version < row.version, sync_error: row?.sync_error ?? null, version: row?.version ?? 0, score: row ? scoreSchema.parse(JSON.parse(row.payload)) : { rules: match.scoring_rules ?? defaultScoringRules, points: [] } as Score, updated_at: row?.updated_at ?? null };
}
export async function savePoint(matchId: string, version: number, action: Side | "undo", actor: string) {
    const current = await readScore(matchId);
    if (current.version !== version) throw new HttpError(409, "比分已在其他裝置更新，已重新載入，請確認後再操作。");
    let score: Score;
    try { score = scorePoint(current.score, action); }
    catch (error) { throw new HttpError(409, (error as Error).message); }
    const now = new Date().toISOString();
    const saved = await database().prepare("INSERT INTO match_scores (match_id,version,payload,updated_at,actor) SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM tournament,json_each(tournament.payload,'$.matches') AS m WHERE tournament.id=1 AND json_extract(m.value,'$.id')=? AND json_extract(m.value,'$.status')='in_progress') ON CONFLICT(match_id) DO UPDATE SET version=excluded.version,payload=excluded.payload,updated_at=excluded.updated_at,actor=excluded.actor,sync_error=NULL WHERE match_scores.version=?").bind(matchId, version + 1, JSON.stringify(score), now, actor, matchId, version).run();
    if (saved.meta.changes !== 1) throw new HttpError(409, "比分或場地已更新，請確認最新狀態後再操作。");
    return { version: version + 1, score, updated_at: now };
}
