import type { Tournament } from "./schedule/schema";
import type { PublicEnvelope } from "./publication-model";

export type PublishedPosition = { start: string; court_id: string; court_name: string } | null;
export type PublicationChange = {
    id: string;
    code: string;
    division: string;
    side_a: string;
    side_b: string;
    kind: "initial" | "added" | "removed" | "moved";
    before: PublishedPosition;
    after: PublishedPosition;
};
export type PublicationReview = { initial: boolean; total: number; changes: PublicationChange[] };

// Compare the acknowledged public schedule, ignoring live status and audit metadata.
export function publicationDiff(previous: Pick<PublicEnvelope, "matches"> | null, next: Tournament): PublicationReview {
    const before = new Map(previous?.matches.map(match => [match.id, match]) ?? []);
    const after = new Map(next.matches.map(match => [match.id, match]));
    const courtName = (id: string) => next.courts.find(court => court.id === id)?.name ?? id;
    const position = (start: string | null, court: string | null): PublishedPosition => start && court ? { start: new Date(start).toISOString(), court_id: court, court_name: courtName(court) } : null;
    const changes: PublicationChange[] = [];
    for (const id of new Set([...before.keys(), ...after.keys()])) {
        const old = before.get(id), current = after.get(id);
        const from = old ? position(old.planned_start_at, old.court_id) : null;
        const to = current?.assignment ? position(new Date(current.assignment.start * 60000).toISOString(), current.assignment.court) : null;
        if (old && current && from?.start === to?.start && from?.court_id === to?.court_id) continue;
        const match = current ?? old!;
        changes.push({ id, code: match.code, division: match.division, side_a: match.side_a, side_b: match.side_b,
            kind: !previous ? "initial" : !old ? "added" : !current ? "removed" : "moved", before: from, after: to });
    }
    const changeTime = (change: PublicationChange) => {
        const value = change.after?.start ?? change.before?.start;
        return value ? Date.parse(value) : Infinity;
    };
    changes.sort((a, b) => changeTime(a) - changeTime(b) || a.code.localeCompare(b.code, undefined, { numeric: true }));
    return { initial: previous === null, total: next.matches.length, changes };
}
