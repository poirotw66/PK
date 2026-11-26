import { stableId } from "./stable-id";
import catalog from "./encouragement-catalog.json";
import type { Tournament, SlotSource } from "./schedule/schema";
import { validateResult } from "./scoring";

export function encouragementSourceDescriptor(state:Tournament) {
    if (state.encouragement_sources) return state.encouragement_sources;
    if (state.source_revision !== catalog.source_revision) return null;
    const players = new Map(state.players.map(player => [player.id, player.name]));
    return {...catalog, divisions:catalog.divisions.map(division => ({...division, first_round:division.first_round.map(match => ({
        ...match,
        side_a:{...match.side_a, name:players.get(match.side_a.player_id) ?? ""},
        side_b:{...match.side_b, name:players.get(match.side_b.player_id) ?? ""},
    }))}))};
}
const canonical=(value:unknown):string=>JSON.stringify(value&&typeof value==="object"?Array.isArray(value)?value.map(item=>JSON.parse(canonical(item))):Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,JSON.parse(canonical(item))])):value);
export function drawBracket(entryIds:string[],seed:number){
    let value=seed>>>0;const random=()=>{value+=0x6D2B79F5;let x=value;x=Math.imul(x^(x>>>15),x|1);x^=x+Math.imul(x^(x>>>7),x|61);return ((x^(x>>>14))>>>0)/4294967296;};
    const entries=[...entryIds].sort();for(let i=entries.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[entries[i],entries[j]]=[entries[j],entries[i]];}
    const capacity=entries.length<2?0:2**Math.ceil(Math.log2(entries.length)),bracket:(string|null)[]=[];
    for(const [index,entry] of entries.entries()){if(!capacity)break;bracket.push(entry);if(index<capacity-entries.length)bracket.push(null);}return bracket;
}
export function drawTopology(divisionId:string,bracket:(string|null)[]){
    let slots:(SlotSource|null)[]=bracket.map(entry=>entry?{kind:"entry",entry_id:entry}:null),round=1;
    const matches:{id:string;round:number;position:number;a:SlotSource;b:SlotSource}[]=[];
    while(slots.length>1){const next:(SlotSource|null)[]=[];for(let index=0;index<slots.length;index+=2){const a=slots[index],b=slots[index+1];if(!a||!b){next.push(a??b);continue;}const position=index/2,id=stableId("elimination",stableId("placement-stage",divisionId,"encouragement"),String(round),String(position));matches.push({id,round,position,a,b});next.push({kind:"winner",match_id:id});}slots=next;round++;}return matches;
}
export function validateEncouragementRecords(state:Tournament){
    const records=state.encouragement_draws??[];if(!records.length)return;
    const descriptor=encouragementSourceDescriptor(state);
    if(!descriptor||descriptor.source_revision!==state.source_revision)throw new Error("鼓勵賽缺少正式首輪來源。");
    for(const draw of records){
        const source=descriptor.divisions.find(source=>source.division_id===draw.division_id);
        if(!source||source.division!==draw.division||source.stage_id!==draw.source_stage_id||draw.id!==stableId("placement-stage",draw.division_id,"encouragement")||state.matches.some(match=>match.division===draw.division&&/循環|round.?robin/i.test(match.stage)))throw new Error("鼓勵賽不是已核定的直接淘汰組別。");
        if(canonical([...draw.source_match_ids].sort())!==canonical(source.first_round.map(row=>row.match_id).sort()))throw new Error("鼓勵賽必須包含該組全部實際首輪來源。");
        const eligible:string[]=[],seenPeople=new Set<string>();
        for(const first of source.first_round){
            const match=state.matches.find(match=>match.id===first.match_id);
            if(!match||match.division!==source.division||match.encouragement||match.status!=="completed"||!match.result||match.players.length!==2)throw new Error("鼓勵賽來源首輪尚未完整確認。");
            for(const [key,side] of [["side_a",first.side_a],["side_b",first.side_b]] as const){if(seenPeople.has(side.player_id)||match[key]!==side.name||!match.players.includes(side.player_id)||!state.players.some(player=>player.id===side.player_id&&player.name===side.name))throw new Error("鼓勵賽首輪選手身分不一致。");seenPeople.add(side.player_id);}
            validateResult(match.result);const outcome=match.result.outcome??"played";if(outcome==="retired")throw new Error("中途退賽的鼓勵賽資格尚未核定。");
            if(outcome==="played"){const loser=match.result.winner==="a"?first.side_b:first.side_a;eligible.push(loser.entry_id);if(!state.entries?.some(entry=>entry.id===loser.entry_id&&entry.player_id===loser.player_id))throw new Error("鼓勵賽參賽身分不一致。");}
        }
        const basis=canonical(source.first_round.map(first=>{const match=state.matches.find(match=>match.id===first.match_id)!;return {id:match.id,status:match.status,result:match.result??null};}).sort((a,b)=>a.id.localeCompare(b.id)));
        if(draw.basis!==basis||canonical([...eligible].sort())!==canonical([...draw.entry_ids].sort()))throw new Error("鼓勵賽資格與首輪成績不一致，不能還原或覆寫。");
        const bracket=drawBracket(eligible,draw.seed);if(canonical(bracket)!==canonical(draw.bracket_entry_ids))throw new Error("鼓勵賽籤表與抽籤種子不一致。");
        const topology=drawTopology(draw.division_id,bracket);
        if(canonical(topology.map(match=>match.id))!==canonical(draw.match_ids))throw new Error("鼓勵賽場次不符合單敗淘汰籤表。");
        for(const item of topology){const expectedDependencies=[item.a,item.b].map(slot=>slot.kind==="entry"?source.first_round.find(first=>[first.side_a.entry_id,first.side_b.entry_id].includes(slot.entry_id))!.match_id:(slot as {match_id:string}).match_id).map(id=>({id,lag:60}));const match=state.matches.find(match=>match.id===item.id);if(!match||match.encouragement?.draw_id!==draw.id||match.encouragement.division_id!==draw.division_id||match.encouragement.stage_id!==draw.id||match.encouragement.round!==item.round||match.encouragement.position!==item.position||canonical(match.slots)!==canonical({a:item.a,b:item.b})||match.division!==draw.division||match.stage!=="鼓勵賽"||match.scoring_rules?.best_of!==3||match.championship||canonical(match.dependencies)!==canonical(expectedDependencies)||match.rest<60||match.scoring_rules.target!==11||match.scoring_rules.margin!==2)throw new Error("鼓勵賽場次拓樸或賽制不一致。");}
    }
}
