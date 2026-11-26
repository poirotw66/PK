import { readTournament } from "./schedule/schema";
import type { Tournament } from "./schedule/schema";
import { defaultScoringRules } from "./scoring";

export function reviewCoachRules(current:Tournament,championshipIds:string[]) {
    const state=readTournament(structuredClone(current)),selected=new Set(championshipIds);
    if(selected.size!==championshipIds.length||championshipIds.some(id=>!state.matches.some(match=>match.id===id)))throw new Error("冠軍賽場次重複或不存在。");
    if(state.matches.some(match=>match.championship!==undefined&&match.championship!==selected.has(match.id)))throw new Error("冠軍賽選擇與已核定籤表不一致，請在地端核對賽制。");
    const changes:{id:string;code:string;before:3|5;after:3|5}[]=[];
    state.coach_profile={version:1,championship_ids:[...selected].sort()};
    for(const match of state.matches) {
        match.championship=selected.has(match.id);
        match.allowed_outcomes=[...new Set([...(match.allowed_outcomes??["played"]),"walkover" as const])];
        if(match.result||match.status==="completed"||match.status==="in_progress")continue;
        const before=match.scoring_rules??defaultScoringRules,after=match.championship?5:3;
        if(before.best_of!==after)changes.push({id:match.id,code:match.code,before:before.best_of,after});
        match.scoring_rules={...before,best_of:after};
    }
    return {state:readTournament(state),changes};
}
