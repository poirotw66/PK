import type { Tournament } from "./schedule/schema";
export type CourtLive = {
    match_id: string | null;
    started_at: string | null;
    previous_status: "planned" | "ready" | null;
};
export type LiveState = {
    courts: Record<string, CourtLive>;
    starts: Record<string, string>;
};
export type PublicationPayload = {
    schedule: Tournament;
    live: LiveState;
};
export type PublicEnvelope = {
    source_id: string;
    sequence: number;
    schedule_revision: string;
    published_at: string;
    live_updated_at: string | null;
    created_at: string;
    matches: {
        id: string;
        code: string;
        division: string;
        side_a: string;
        side_b: string;
        status: string;
        court_id: string | null;
        planned_start_at: string | null;
        started_at: string | null;
    }[];
};
export function emptyLive(state: Tournament): LiveState {
    return { courts: Object.fromEntries(state.courts.map(court => [court.id, { match_id: null, started_at: null, previous_status: null }])), starts: {} };
}
export function envelope(payload: PublicationPayload, metadata: Omit<PublicEnvelope, "matches">): PublicEnvelope {
    return { ...metadata, matches: payload.schedule.matches.map(match => ({
            id: match.id, code: match.code, division: match.division, side_a: match.side_a, side_b: match.side_b, status: match.status,
            court_id: match.assignment?.court ?? null,
            planned_start_at: match.assignment ? new Date(match.assignment.start * 60000).toISOString() : null,
            started_at: payload.live.starts[match.id] ?? null,
        })) };
}
export function transitionLive(main: Tournament, published: PublicationPayload, input: {
    action: "start" | "end" | "cancel";
    court_id: string;
    match_id: string;
}, now: string) {
    const current = published.live.courts[input.court_id];
    if (!current)
        throw new Error("場地不存在。");
    const canonical = structuredClone(main), next = structuredClone(published);
    const match = canonical.matches.find(row => row.id === input.match_id), visible = next.schedule.matches.find(row => row.id === input.match_id);
    if (!match || !visible)
        throw new Error("場次不在已發布賽程內。");
    if (input.action === "start") {
        if (current.match_id)
            throw new Error("此場地已有比賽進行中，請先結束或撤銷開賽。");
        if (!["planned", "ready"].includes(match.status) || !["planned", "ready"].includes(visible.status))
            throw new Error("這場比賽已開打或已結束。");
        if (!match.assignment || !visible.assignment || match.assignment.court !== input.court_id || visible.assignment.court !== input.court_id
            || match.assignment.start !== visible.assignment.start)
            throw new Error("這場有未發布的調整，請先發布最新賽程。");
        if (match.players.length !== 2)
            throw new Error("雙方選手尚未確定，不能標記開賽。");
        for (const row of Object.values(published.live.courts)) {
            if (!row.match_id)
                continue;
            const active = main.matches.find(other => other.id === row.match_id);
            if (active?.players.some(id => match.players.includes(id)))
                throw new Error("選手已在其他場地比賽，請先確認現場狀態。");
        }
        next.live.courts[input.court_id] = { match_id: match.id, started_at: now, previous_status: match.status as "planned" | "ready" };
        next.live.starts[match.id] = now;
        match.status = "in_progress";
        visible.status = "in_progress";
    }
    else {
        if (current.match_id !== match.id || match.status !== "in_progress")
            throw new Error("場地狀態已改變，請重新整理。");
        match.status = input.action === "end" ? "completed" : current.previous_status ?? "planned";
        visible.status = match.status;
        if (input.action === "cancel")
            delete next.live.starts[match.id];
        next.live.courts[input.court_id] = { match_id: null, started_at: null, previous_status: null };
    }
    return { main: canonical, publication: next };
}
