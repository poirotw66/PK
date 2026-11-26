import type { Tournament } from "./schedule/schema";
export const dispatchLimit = 3;
export function dispatchQueues(state: Tournament, stored: Record<string,string[]> = {}) {
    const pending = new Set(state.matches.filter(match => !match.result && match.status !== "completed").map(match => match.id));
    const used = new Set<string>();
    return Object.fromEntries(state.courts.map(court => [court.id, (stored[court.id] ?? []).filter(id => { if (!pending.has(id) || used.has(id)) return false; used.add(id); return true; }).slice(0,dispatchLimit)]));
}
export function dispatchHeadWarnings(state: Tournament, queues: Record<string,string[]>) {
    const heads = state.courts.map(court => ({ court, match: state.matches.find(match => match.id === queues[court.id]?.[0]) }));
    return Object.fromEntries(heads.map(({ court, match }) => {
        if (!match) return [court.id, null];
        if (match.players.length !== 2) return [court.id, "雙方選手尚未確認，請先核對晉級或調整候場順序。"];
        const conflicts = heads.filter(other => other.court.id !== court.id && other.match?.players.some(player => match.players.includes(player)));
        return [court.id, conflicts.length ? `選手也列在${conflicts.map(other => other.court.name).join("、")}的第一場，請調整候場順序。` : null];
    }));
}
export function planDispatch(state: Tournament, stored: Record<string,string[]> | undefined, matchId: string, courtId: string | null, position?: number) {
    const match = state.matches.find(match => match.id === matchId);
    if (!match || match.result || match.status === "completed") throw new Error("此場已登錄成績或不存在，不能排入場地。");
    if (courtId && !state.courts.some(court => court.id === courtId)) throw new Error("找不到這個場地。");
    const queues = dispatchQueues(state, stored);
    for (const queue of Object.values(queues)) { const index=queue.indexOf(matchId);if(index>=0)queue.splice(index,1); }
    if (courtId) {
        const queue=queues[courtId];
        if(queue.length>=dispatchLimit) throw new Error("每個場地最多排入三場，請先完成或移除場次。");
        queue.splice(Math.min(position ?? queue.length,queue.length),0,matchId);
    }
    const heads=Object.entries(queues).flatMap(([court,ids])=>ids.length?[{court,match:state.matches.find(match=>match.id===ids[0])!}]:[]);
    for(let i=0;i<heads.length;i++)for(let j=i+1;j<heads.length;j++)if(heads[i].match.players.some(player=>heads[j].match.players.includes(player)))throw new Error("同一位選手不能同時安排在兩個場地的第一場，請調整候場順序。");
    return queues;
}
