import { readTournament } from "./schedule/schema";
import type { Tournament } from "./schedule/schema";
import type { LiveState, PublicEnvelope } from "./publication-model";

type ReturnInput = {
    state: Tournament;
    cloud_revision: string;
    origin: string;
    exported_at: string;
    sequence: number;
    live: LiveState | null;
    start_history: PublicEnvelope[];
};

export function canonicalJson(value: unknown): string {
    if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
    if (value !== null && typeof value === "object") {
        const object = value as Record<string, unknown>;
        return "{" + Object.keys(object).sort().map(key => JSON.stringify(key) + ":" + canonicalJson(object[key])).join(",") + "}";
    }
    return JSON.stringify(value);
}

export async function canonicalDigest(value: unknown) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function buildCloudReturn(input: ReturnInput) {
    const state = readTournament(input.state);
    if (state.matches.some(match => match.status === "in_progress") || Object.values(input.live?.courts ?? {}).some(court => court.match_id))
        throw new Error("仍有比賽進行中，請先確認完賽成績或撤銷誤開賽，再下載地端交接檔。");
    if (state.matches.some(match => match.status === "completed" && !match.result))
        throw new Error("已結束的比賽缺少確認成績，請先補齊成績再下載地端交接檔。");
    const matchIds = new Set(state.matches.map(match => match.id));
    const courtIds = new Set(state.courts.map(court => court.id));
    const starts = { ...input.live?.starts };
    const actual_courts: Record<string, string> = {};
    for (const [matchId, startedAt] of Object.entries(starts)) {
        if (!matchIds.has(matchId) || !Number.isFinite(Date.parse(startedAt)))
            throw new Error("實際開賽紀錄無效，無法建立地端交接檔。");
        let courtId = input.live?.actual_courts?.[matchId];
        if (!courtId) {
            for (const publication of input.start_history) {
                const recorded = publication.matches.find(match => match.id === matchId && match.started_at === startedAt && match.status === "in_progress");
                if (recorded?.court_id) courtId = recorded.court_id;
            }
        }
        if (!courtId || !courtIds.has(courtId))
            throw new Error("歷史開賽紀錄缺少實際場地，請保留雲端資料並先核對現場紀錄。");
        actual_courts[matchId] = courtId;
    }
    const payload = {
        format: "pk-cloud-return-v1" as const,
        source_revision: state.source_revision,
        cloud_revision: input.cloud_revision,
        origin: input.origin,
        exported_at: input.exported_at,
        sequence: input.sequence,
        state,
        live: { starts, actual_courts },
    };
    const sha256 = await canonicalDigest(payload);
    return { ...payload, sha256 };
}
