import type { Match, Tournament, Update } from "./schema";
import type { Issue } from "./rules";
import { minimumRestBetween, review } from "./rules";
export type SwapReview = {
    updates: Update[];
    changes: ReturnType<typeof review>["changes"];
    issues: Issue[];
    can_apply: boolean;
    error: string | null;
};
export function reviewSwap(state: Tournament, firstId: string, secondId: string): SwapReview {
    const invalid = (error: string): SwapReview => ({ updates: [], changes: [], issues: [], can_apply: false, error });
    if (firstId === secondId)
        return invalid("請選擇另一場比賽交換。");
    const first = state.matches.find(match => match.id === firstId), second = state.matches.find(match => match.id === secondId);
    if (!first?.assignment || !second?.assignment)
        return invalid("兩場比賽都必須已排程，才能交換位置。");
    const updates = [{ id: firstId, ...second.assignment }, { id: secondId, ...first.assignment }];
    try {
        const result = review(state, updates);
        return { updates, changes: result.changes, issues: [...result.introduced, ...result.remaining], can_apply: result.can_apply, error: null };
    }
    catch (error) {
        return invalid(error instanceof Error ? error.message : "無法交換這兩場比賽。");
    }
}
export type Neighbor = {
    match: Match;
    gap: number;
    required: number;
    sufficient: boolean;
};
export type PlayerTimeline = {
    id: string;
    name: string;
    previous: Neighbor | null;
    next: Neighbor | null;
};
export function playerTimelines(state: Tournament, matchId: string): PlayerTimeline[] {
    const selected = state.matches.find(match => match.id === matchId);
    if (!selected)
        return [];
    return selected.players.map(id => {
        const player = state.players.find(row => row.id === id);
        const empty: PlayerTimeline = { id, name: player?.name ?? "未確認選手", previous: null, next: null };
        if (!selected.assignment)
            return empty;
        const scheduled = state.matches.filter(match => match.assignment && match.players.includes(id))
            .sort((a, b) => a.assignment!.start - b.assignment!.start || a.id.localeCompare(b.id));
        const index = scheduled.findIndex(match => match.id === matchId);
        function neighbor(other: Match | undefined, previous: boolean): Neighbor | null {
            if (!other?.assignment || !selected?.assignment)
                return null;
            const first = previous ? other : selected, second = previous ? selected : other;
            const gap = second.assignment!.start - first.assignment!.start - first.play;
            const required = minimumRestBetween(state, first, second, [id]);
            return { match: other, gap, required, sufficient: gap >= required };
        }
        return { ...empty, previous: neighbor(scheduled[index - 1], true), next: neighbor(scheduled[index + 1], false) };
    });
}
