export type QueueMatch = { id: string; planned_start: string | null; eligible?: boolean };
export function taipeiDate(value: Date | string = new Date()): string {
    const date = typeof value === "string" ? new Date(value) : value;
    if (!Number.isFinite(date.getTime())) return "";
    const parts = new Intl.DateTimeFormat("en", {timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(date);
    const part = (type: string) => parts.find(item => item.type === type)?.value;
    return `${part("year")}-${part("month")}-${part("day")}`;
}
export function preferredQueueDate(dates: readonly string[], today = taipeiDate()): string {
    const sorted = [...new Set(dates)].sort();
    return sorted.includes(today) ? today : sorted.find(date => date > today) ?? sorted.at(-1) ?? today;
}
export function queueForDate<T extends QueueMatch>(matches: readonly T[], date: string): T[] {
    return matches.filter(match => match.planned_start && taipeiDate(match.planned_start) === date)
        .sort((a,b) => Date.parse(a.planned_start!) - Date.parse(b.planned_start!) || a.id.localeCompare(b.id));
}
