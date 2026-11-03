import type { Match, Tournament, Update } from "./schema";
import { localTime, parseLocal, validate } from "./rules";
export type Placement = {
    start: number;
    court: string;
    kind: "available" | "occupied" | "blocked" | "current";
    message: string;
    messages?: string[];
};
export function withDraft(state: Tournament, draft: Record<string, Update>): Tournament {
    return {
        ...state,
        matches: state.matches.map(match => ({
            ...match,
            assignment: draft[match.id] ? { start: draft[match.id].start, court: draft[match.id].court } : match.assignment,
        })),
    };
}
export function mergeDraft(state: Tournament, draft: Record<string, Update>, updates: Update[]): Record<string, Update> {
    const next = { ...draft };
    for (const update of updates) {
        const original = state.matches.find(match => match.id === update.id)?.assignment;
        if (original?.start === update.start && original.court === update.court)
            delete next[update.id];
        else
            next[update.id] = update;
    }
    return next;
}
export function editable(match: Match): boolean {
    return !["completed", "in_progress"].includes(match.status) && !(match.lock_time && match.lock_court);
}
export function placementOptions(state: Tournament, matchId: string, day: string): Placement[] {
    const match = state.matches.find(row => row.id === matchId);
    if (!match || !editable(match) || !state.dates.includes(day))
        return [];
    const scope = new Set([matchId]);
    const first = parseLocal(day + "T" + state.first_start);
    const last = parseLocal(day + "T" + state.last_start);
    const options: Placement[] = [];
    for (let start = first; start <= last; start += state.grid) {
        for (const court of state.courts) {
            if (match.assignment?.start === start && match.assignment.court === court.id) {
                options.push({ start, court: court.id, kind: "current", message: "目前位置" });
                continue;
            }
            if ((match.lock_time && match.assignment?.start !== start) || (match.lock_court && match.assignment?.court !== court.id)) {
                options.push({ start, court: court.id, kind: "blocked", message: "此場次的時間或場地已鎖定" });
                continue;
            }
            const occupied = state.matches.some(other => other.id !== matchId && other.assignment?.court === court.id
                && other.assignment.start < start + match.play + match.buffer
                && other.assignment.start + other.play + other.buffer > start);
            if (occupied) {
                options.push({ start, court: court.id, kind: "occupied", message: "場地已有比賽或整理緩衝" });
                continue;
            }
            const candidate = {
                ...state,
                matches: state.matches.map(other => other.id === matchId ? { ...other, assignment: { start, court: court.id } } : other),
            };
            const issues = validate(candidate, scope);
            options.push({ start, court: court.id, kind: issues.length ? "blocked" : "available", message: issues[0]?.message ?? "可移入這個空檔", messages: issues.map(issue => issue.message) });
        }
    }
    return options;
}
export function nearestOptions(options: Placement[], currentStart: number | undefined, limit = 6): Placement[] {
    return options.filter(option => option.kind === "available")
        .sort((a, b) => Math.abs(a.start - (currentStart ?? a.start)) - Math.abs(b.start - (currentStart ?? b.start))
        || a.start - b.start || a.court.localeCompare(b.court))
        .slice(0, limit);
}
export function occupies(match: Match, start: number, court: string): boolean {
    return match.assignment?.court === court && match.assignment.start <= start
        && match.assignment.start + match.play + match.buffer > start;
}
export function clock(minute: number): string {
    return localTime(minute).slice(11);
}
export function nextAvailablePosition(options: Placement[], start: number, court: string): Placement | null {
    const available = options.filter(option => option.kind === "available" && option.start >= start);
    const sameCourt = available.filter(option => option.court === court && option.start > start)
        .sort((a, b) => a.start - b.start);
    if (sameCourt.length)
        return sameCourt[0];
    return available.filter(option => option.court !== court)
        .sort((a, b) => a.start - b.start || a.court.localeCompare(b.court))[0] ?? null;
}
