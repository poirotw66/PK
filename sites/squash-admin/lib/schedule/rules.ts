import type { Tournament, Match, Update } from "./schema";
import { updatesSchema } from "./schema";
export type Issue = {
    key: string;
    code: string;
    message: string;
    matches: string[];
};
export function localTime(minute: number): string {
    return new Date((minute + 480) * 60000).toISOString().slice(0, 16);
}
export function parseLocal(value: string): number {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
        throw new Error("時間格式無效。");
    const result = Date.parse(value + ":00+08:00") / 60000;
    if (!Number.isInteger(result) || localTime(result) !== value)
        throw new Error("時間不存在。");
    return result;
}
export function minimumRestBetween(state: Tournament, first: Match, second: Match, playerIds: string[]): number {
    const rest = Math.max(state.minimum_rest, first.rest, second.rest, ...playerIds.map(id => state.players.find(player => player.id === id)?.rest ?? 0));
    const transfer = first.assignment && second.assignment && first.assignment.court !== second.assignment.court
        ? Math.max(first.transfer, second.transfer) : 0;
    return rest + transfer;
}
export function validate(state: Tournament, scope?: ReadonlySet<string>): Issue[] {
    const issues: Issue[] = [];
    const add = (code: string, ids: string[], message: string, extra = "") => {
        if (!scope || ids.some(id => scope.has(id))) {
            issues.push({ key: JSON.stringify([code, [...ids].sort(), extra]), code, matches: ids, message });
        }
    };
    const map = new Map(state.matches.map(m => [m.id, m]));
    const assigned = state.matches.filter((m): m is Match & {
        assignment: NonNullable<Match["assignment"]>;
    } => m.assignment !== null);
    const daily = new Map<string, Match[]>(), groups = new Map<string, Match[]>();
    for (const m of state.matches) {
        const a = m.assignment;
        if (!a) {
            add("MISSING", [m.id], `第 ${m.code} 場尚未安排。`);
            continue;
        }
        const local = localTime(a.start), day = local.slice(0, 10), clock = local.slice(11), anchor = state.anchors[day];
        if (!state.dates.includes(day) || clock < state.first_start || clock > state.last_start)
            add("START_WINDOW", [m.id], `第 ${m.code} 場須在比賽日 08:00–17:30 開始。`);
        if (anchor === undefined || a.start < anchor || (a.start - anchor) % state.grid)
            add("GRID", [m.id], `第 ${m.code} 場不在 ${state.grid} 分鐘時段上。`);
        const court = state.courts.find(c => c.id === a.court);
        if (!court?.windows.some(w => w.start <= a.start && a.start + m.play + m.buffer <= w.end))
            add("COURT_WINDOW", [m.id], `第 ${m.code} 場超出場地開放時間或遇到休場。`);
        if (m.ready_after !== null && a.start < m.ready_after)
            add("DEPENDENCY", [m.id], `第 ${m.code} 場早於抽籤完成時間。`);
        for (const dependency of m.dependencies) {
            const prior = map.get(dependency.id);
            if (!prior?.assignment || a.start < prior.assignment.start + prior.play + dependency.lag)
                add("DEPENDENCY", [m.id, dependency.id], `第 ${m.code} 場須等第 ${prior?.code ?? "待確認"} 場結束並完成確認。`);
        }
        for (const player of m.players) {
            const key = player + "/" + day;
            daily.set(key, [...(daily.get(key) ?? []), m]);
        }
        if (m.group)
            groups.set(m.group, [...(groups.get(m.group) ?? []), m]);
    }
    const sources = scope ? assigned.filter(match => scope.has(match.id)) : assigned;
    for (let i = 0; i < sources.length; i++) {
        const targets = scope
            ? assigned.filter(match => match.id !== sources[i].id && (!scope.has(match.id) || sources[i].id < match.id))
            : assigned.slice(i + 1);
        for (const target of targets) {
            let a = sources[i], b = target;
            if (a.assignment.start > b.assignment.start)
                [a, b] = [b, a];
            if (a.assignment.court === b.assignment.court && b.assignment.start < a.assignment.start + a.play + a.buffer)
                add("COURT_OVERLAP", [a.id, b.id], `第 ${a.code} 與 ${b.code} 場的比賽／場地緩衝重疊。`);
            const shared = a.players.filter(p => b.players.includes(p));
            if (shared.length) {
                const required = minimumRestBetween(state, a, b, shared);
                const gap = b.assignment.start - a.assignment.start - a.play;
                if (gap < required)
                    add("PLAYER_REST", [a.id, b.id], `${shared.map(id => state.players.find(p => p.id === id)?.name).join("、")}：第 ${a.code} → ${b.code} 場休息 ${gap} 分鐘，至少需要 ${required} 分鐘。`);
            }
        }
    }
    for (const [key, matches] of daily)
        if (matches.length > state.daily_limit)
            add("DAILY_LIMIT", matches.map(m => m.id), `${state.players.find(p => p.id === key.split("/")[0])?.name} 在 ${key.split("/")[1]} 有 ${matches.length} 場，上限 ${state.daily_limit} 場。`, key);
    if (state.keep_groups_same_day)
        for (const [group, matches] of groups)
            if (new Set(matches.map(m => localTime(m.assignment!.start).slice(0, 10))).size > 1)
                add("GROUP_SPLIT", matches.map(m => m.id), `${matches[0].division} 的同一循環賽小組不可跨日。`, group);
    return issues;
}
export function review(before: Tournament, input: unknown) {
    const updates = updatesSchema.parse(input);
    if (new Set(updates.map(u => u.id)).size !== updates.length)
        throw new Error("同一場比賽不可重複修改。");
    const after = structuredClone(before), changes: {
        id: string;
        code: string;
        before: Match["assignment"];
        after: Update;
    }[] = [];
    for (const update of updates) {
        const match = after.matches.find(m => m.id === update.id);
        if (!match || ["completed", "in_progress"].includes(match.status))
            throw new Error("場次不存在、已開打或已完成，不能修改。");
        if (!before.courts.some(c => c.id === update.court))
            throw new Error("場地不存在。");
        if (match.lock_time && match.assignment?.start !== update.start || match.lock_court && match.assignment?.court !== update.court)
            throw new Error(`第 ${match.code} 場已鎖定，不能修改。`);
        if (match.assignment?.start === update.start && match.assignment?.court === update.court)
            continue;
        changes.push({ id: match.id, code: match.code, before: match.assignment, after: update });
        match.assignment = { start: update.start, court: update.court };
    }
    if (!changes.length)
        throw new Error("沒有需要儲存的變更。");
    const baseline = new Set(validate(before).map(i => i.key)), issues = validate(after);
    const introduced = issues.filter(i => !baseline.has(i.key)), remaining = issues.filter(i => baseline.has(i.key));
    // Existing conflicts must be repaired before publishing another revision.
    return { after, changes, introduced, remaining, can_apply: issues.length === 0 };
}
