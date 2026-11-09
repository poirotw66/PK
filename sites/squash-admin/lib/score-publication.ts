import { env } from "cloudflare:workers";
import { database } from "./auth";
import { scoreSchema, tally } from "./scoring";
import type { PublicationPayload } from "./publication-model";
export async function flushScores(matchId?: string) {
    const db = database(), state = await db.prepare("SELECT payload,ack_sequence,sequence FROM public_state WHERE id=1").first<{payload:string;ack_sequence:number;sequence:number}>();
    if (!state || state.ack_sequence < state.sequence) return;
    const publication = JSON.parse(state.payload) as PublicationPayload;
    const pending = await db.prepare("SELECT match_id,version,payload,updated_at FROM match_scores WHERE synced_version<version AND (? IS NULL OR match_id=?)").bind(matchId??null,matchId??null).all<{match_id:string;version:number;payload:string;updated_at:string}>();
    for (const row of pending.results) {
        const active = Object.values(publication.live.courts).find(court => court.match_id === row.match_id);
        if (!active?.started_at) continue;
        try {
            if (!env.CLOUD_PUBLISH_SECRET || !env.PUBLISH_SOURCE_ID || !env.READER_ORIGIN) throw new Error("Missing score publication configuration");
            const origin = new URL(env.READER_ORIGIN);
            if (origin.protocol !== "https:" && !(import.meta.env.DEV && ["127.0.0.1","localhost"].includes(origin.hostname))) throw new Error("Invalid reader origin");
            const score = scoreSchema.parse(JSON.parse(row.payload)), summary = tally(score);
            const record = {match_id:row.match_id,started_at:active.started_at,version:row.version,updated_at:row.updated_at,score:{rules:score.rules,games:summary.games,current:summary.current,winner:summary.winner}};
            const raw = JSON.stringify({source_id:env.PUBLISH_SOURCE_ID,record}), path = "/sync/cloud-score", timestamp = String(Math.floor(Date.now()/1000));
            const key = await crypto.subtle.importKey("raw",new TextEncoder().encode(env.CLOUD_PUBLISH_SECRET),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
            const signature = Array.from(new Uint8Array(await crypto.subtle.sign("HMAC",key,new TextEncoder().encode(timestamp+"\n"+path+"\n"+raw))),byte=>byte.toString(16).padStart(2,"0")).join("");
            const response = await fetch(new URL(path,origin),{method:"POST",redirect:"manual",signal:AbortSignal.timeout(4000),headers:{"Content-Type":"application/json","X-Sync-Time":timestamp,"X-Sync-Signature":signature},body:raw});
            const ack = await response.json() as {source_id?:string;match_id?:string;started_at?:string;version?:number};
            if (!response.ok || ack.source_id !== env.PUBLISH_SOURCE_ID || ack.match_id !== row.match_id || ack.started_at !== active.started_at || !Number.isSafeInteger(ack.version) || ack.version! < row.version) throw new Error("Live score was not acknowledged");
            await db.prepare("UPDATE match_scores SET synced_version=?,sync_error=NULL WHERE match_id=? AND version=?").bind(row.version,row.match_id,row.version).run();
        } catch {
            await db.prepare("UPDATE match_scores SET sync_error=? WHERE match_id=? AND version=?").bind("比分已儲存，賽場看板尚未收到更新，將自動重試。",row.match_id,row.version).run();
        }
    }
}
