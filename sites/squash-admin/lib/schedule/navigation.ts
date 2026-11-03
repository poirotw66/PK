import type { Match, Tournament, Update } from "./schema";
import { localTime } from "./rules";
import { withDraft } from "./placements";
function normalized(value: string): string {
    return value.normalize("NFKC").trim().toLocaleLowerCase();
}
export function searchMatches(state: Tournament, draft: Record<string, Update>, query: string): Match[] {
    const text = normalized(query);
    if (!text)
        return [];
    const matches = withDraft(state, draft).matches;
    const exact = matches.filter(match => normalized(match.code) === text);
    const terms = text.split(/\s+/);
    return (exact.length ? exact : matches.filter(match => {
        const court = state.courts.find(row => row.id === match.assignment?.court)?.name ?? "";
        const content = normalized([match.code, match.side_a, match.side_b, match.division, match.stage,
            match.assignment ? localTime(match.assignment.start) : "未排程", court].join(" "));
        return terms.every(term => content.includes(term));
    })).sort((a, b) => (a.assignment?.start ?? Infinity) - (b.assignment?.start ?? Infinity)
        || a.code.localeCompare(b.code, undefined, { numeric: true }));
}
