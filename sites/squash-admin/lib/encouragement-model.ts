import { stableId } from "./stable-id";
import { drawBracket, encouragementSourceDescriptor } from "./encouragement-record";
import { canonicalJson } from "./local-return-model";
import { readTournament, encouragementSourcesSchema } from "./schedule/schema";
import type { Tournament, Match, SlotSource } from "./schedule/schema";
import { localTime, minimumRestBetween, validate } from "./schedule/rules";
import type { Issue } from "./schedule/rules";
import { validateResult } from "./scoring";

type Source = NonNullable<Tournament["encouragement_sources"]>["divisions"][number];
type Entrant = {entry_id:string;player_id:string;name:string;source_match_id:string;source_code:string};
export type EncouragementQualification = {division_id:string;division:string;status:"waiting"|"ready"|"drawn"|"blocked"|"insufficient";total:number;completed:number;pending:{id:string;code:string}[];eligible:Entrant[];excluded:{code:string;reason:string}[];blockers:string[];draw?:NonNullable<Tournament["encouragement_draws"]>[number]};
export function encouragementSources(state:Tournament):Source[] {
    const value=encouragementSourceDescriptor(state);
    if(!value)return [];
    const sources=encouragementSourcesSchema.parse(value);
    if(sources.source_revision!==state.source_revision)throw new Error("鼓勵賽來源版本與賽事不一致，請重新核對地端交接。");
    if(new Set(sources.divisions.map(row=>row.division_id)).size!==sources.divisions.length)throw new Error("鼓勵賽來源組別重複。");
    return sources.divisions;
}
function checkedSource(state:Tournament,source:Source) {
    const used=new Set<string>(),people=new Set<string>();
    if(state.matches.some(match=>match.division===source.division && /循環|round.?robin/i.test(match.stage)))throw new Error("先打循環賽的組別不辦鼓勵賽。");
    for(const row of source.first_round){
        const match=state.matches.find(match=>match.id===row.match_id);
        if(used.has(row.match_id)||!match||match.division!==source.division||match.encouragement)throw new Error("鼓勵賽首輪來源不存在、重複或組別不符。");
        used.add(row.match_id);
        for(const [key,side] of [["side_a",row.side_a],["side_b",row.side_b]] as const){
            const player=state.players.find(player=>player.id===side.player_id);
            if(!player||player.name!==side.name||match[key]!==side.name||!match.players.includes(side.player_id)||people.has(side.player_id))throw new Error("鼓勵賽首輪來源的選手身分不一致。");
            people.add(side.player_id);
        }
        if(match.players.length!==2||row.side_a.entry_id===row.side_b.entry_id)throw new Error("鼓勵賽來源必須是首輪實際雙人對戰，不能包含輪空。");
    }
}
export function encouragementBasis(state:Tournament,source:Source) {
    return canonicalJson(source.first_round.map(row=>{const match=state.matches.find(match=>match.id===row.match_id)!;return {id:match.id,status:match.status,result:match.result??null};}).sort((a,b)=>a.id.localeCompare(b.id)));
}
export function qualification(state:Tournament,source:Source):EncouragementQualification {
    const row:EncouragementQualification={division_id:source.division_id,division:source.division,status:"waiting",total:source.first_round.length,completed:0,pending:[],eligible:[],excluded:[],blockers:[]};
    try{checkedSource(state,source);}catch(error){row.status="blocked";row.blockers.push(error instanceof Error?error.message:"來源無效。");return row;}
    for(const first of source.first_round){
        const match=state.matches.find(match=>match.id===first.match_id)!;
        if(match.status!=="completed"||!match.result){row.pending.push({id:match.id,code:match.code});continue;}
        try{validateResult(match.result);}catch{row.blockers.push(`第 ${match.code} 場成績無效，請先核對。`);continue;}
        row.completed++;
        const outcome=match.result.outcome??"played";
        if(outcome==="retired"){row.blockers.push(`第 ${match.code} 場中途退賽，鼓勵賽資格須先人工核對。`);continue;}
        if(outcome!=="played"){row.excluded.push({code:match.code,reason:"未實際完賽的棄賽／雙方棄賽不列入鼓勵賽資格。"});continue;}
        const loser=match.result.winner==="a"?first.side_b:first.side_a;
        row.eligible.push({...loser,source_match_id:match.id,source_code:match.code});
    }
    const draw=state.encouragement_draws?.find(draw=>draw.division_id===source.division_id);
    if(draw){row.draw=draw;if(draw.basis!==encouragementBasis(state,source))row.blockers.push("首輪結果與既有鼓勵籤表資格依據不一致，請核對；不能重複抽籤。");}
    row.status=row.blockers.length?"blocked":draw?"drawn":row.pending.length?"waiting":row.eligible.length<2?"insufficient":"ready";
    return row;
}
export function encouragementStatus(state:Tournament) {
    const sources=encouragementSources(state),divisions=sources.map(source=>qualification(state,source));
    return {divisions,unsupported:[...new Set(state.matches.map(match=>match.division))].filter(division=>!sources.some(source=>source.division===division))};
}
function bracketGeometry(state:Tournament,source:Source,q:EncouragementQualification,seed:number,generatedAt:string){
    const drawId=stableId("placement-stage",source.division_id,"encouragement"),stageId=drawId;
    const bracket=drawBracket(q.eligible.map(entry=>entry.entry_id),seed);
    const entries=new Map(q.eligible.map(entry=>[entry.entry_id,entry]));
    const candidates=new Map<string,string[]>(),newMatches:Match[]=[];
    let slots:(SlotSource|null)[]=bracket.map(entry=>entry?{kind:"entry",entry_id:entry}:null),round=1;
    while(slots.length>1){const next:(SlotSource|null)[]=[];for(let index=0;index<slots.length;index+=2){const a=slots[index],b=slots[index+1];if(!a||!b){next.push(a??b);continue;}
        const position=index/2,id=stableId("elimination",stageId,String(round),String(position)),code=`${source.division==="國中男子組"?"國男鼓":source.division==="國中女子組"?"國女鼓":source.division+"鼓"}${newMatches.length+1}`;
        const name=(slot:SlotSource)=>slot.kind==="entry"?entries.get(slot.entry_id)!.name:`${newMatches.find(match=>match.id===(slot as {match_id:string}).match_id)!.code}勝者`;
        const possible=(slot:SlotSource):string[]=>slot.kind==="entry"?[entries.get(slot.entry_id)!.player_id]:candidates.get((slot as {match_id:string}).match_id)!;
        const people=[...new Set([...possible(a),...possible(b)])];candidates.set(id,people);
        const ids={a:a.kind==="entry"?a.entry_id:null,b:b.kind==="entry"?b.entry_id:null};
        const players=Object.values(ids).filter((id):id is string=>!!id).map(id=>entries.get(id)!.player_id).sort();
        const dependencyIds=Object.values({a,b}).flatMap(slot=>slot.kind==="entry"?[entries.get(slot.entry_id)!.source_match_id]:[(slot as {match_id:string}).match_id]);
        newMatches.push({id,code,division:source.division,stage:"鼓勵賽",group:null,side_a:name(a),side_b:name(b),slots:{a,b},entry_ids:ids,players,status:"planned",championship:false,allowed_outcomes:["played","walkover"],scoring_rules:{best_of:3,target:11,margin:2},play:10,buffer:5,rest:60,transfer:0,dependencies:dependencyIds.map(id=>({id,lag:60})),ready_after:null,lock_time:false,lock_court:false,assignment:null,encouragement:{draw_id:drawId,division_id:source.division_id,stage_id:stageId,round,position}});
        next.push({kind:"winner",match_id:id});
    }slots=next;round++;}
    const draw={id:drawId,division_id:source.division_id,division:source.division,source_stage_id:source.stage_id,source_match_ids:source.first_round.map(match=>match.match_id),seed,basis:encouragementBasis(state,source),entry_ids:q.eligible.map(entry=>entry.entry_id).sort(),bracket_entry_ids:bracket,match_ids:newMatches.map(match=>match.id),generated_at:generatedAt};
    return {draw,matches:newMatches,candidates};
}
function scheduleNewMatches(state:Tournament,source:Source,q:EncouragementQualification,matches:Match[],newCandidates:Map<string,string[]>):Issue[]{
    if(!matches.length)return [];
    const deadline=Date.now()+1500;let choiceCount=0;
    const searchLimit=():Issue[]=>[{key:"encouragement-search-limit",code:"SEARCH_LIMIT",matches:matches.map(match=>match.id),message:"鼓勵賽排程搜尋已達上限，尚未找到可驗證安排；請先在地端調整可用時段。"}];
    const eligible=new Set(q.eligible.map(entry=>entry.player_id));
    const divisionPeople=new Map<string,Set<string>>();for(const match of state.matches){const ids=divisionPeople.get(match.division)??new Set<string>();for(const player of match.players)ids.add(player);divisionPeople.set(match.division,ids);}
    const entryPeople=new Map((state.entries??[]).map(entry=>[entry.id,entry.player_id]));
    for(const descriptor of encouragementSources(state))for(const first of descriptor.first_round)for(const side of [first.side_a,first.side_b])entryPeople.set(side.entry_id,side.player_id);
    const byCode=new Map(state.matches.map(match=>[match.code,match]));
    function possible(match:Match,seen=new Set<string>()):string[]{
        if(seen.has(match.id))return [];
        const next=new Set([...seen,match.id]);
        function side(slot:SlotSource|undefined,name:string):string[]{
            if(slot?.kind==="entry")return entryPeople.has(slot.entry_id)?[entryPeople.get(slot.entry_id)!]:[];
            const prior=slot&&(slot.kind==="winner"||slot.kind==="loser")?state.matches.find(row=>row.id===slot.match_id):undefined;
            if(prior)return fromPrior(prior,slot!.kind==="loser");
            const reference=/^(.+?)場(勝者|敗者)$/.exec(name),legacy=reference?byCode.get(reference[1]):undefined;
            if(legacy)return fromPrior(legacy,reference![2]==="敗者");
            const direct=state.players.find(player=>player.name===name&&match.players.includes(player.id));return direct?[direct.id]:[];
        }
        function fromPrior(prior:Match,loser:boolean):string[]{
            if(prior.result?.winner&&prior.status==="completed"){
                const key=loser?(prior.result.winner==="a"?"b":"a"):prior.result.winner;
                const identity=prior.entry_ids?.[key],entry=identity?entryPeople.get(identity):undefined;
                if(entry)return [entry];
                const name=key==="a"?prior.side_a:prior.side_b,player=state.players.find(player=>player.name===name&&prior.players.includes(player.id));if(player)return [player.id];
            }
            return possible(prior,next);
        }
        let ids=[...new Set([...side(match.slots?.a,match.side_a),...side(match.slots?.b,match.side_b)])];
        // A partially resolved side still needs every possible opponent for scheduling.
        const unknownA=!side(match.slots?.a,match.side_a).length,unknownB=!side(match.slots?.b,match.side_b).length;
        if(unknownA||unknownB)ids=[...new Set([...ids,...(divisionPeople.get(match.division)??[])])];
        if(match.division===source.division&&!match.result&&match.status!=="in_progress"&&!match.encouragement&&match.players.length<2)ids=ids.filter(id=>!eligible.has(id));
        return ids;
    }
    const old=state.matches.map(match=>({...match,players:possible(match)}));
    const work=new Map<string,Match>(old.map(match=>[match.id,match]));
    const choices=new Map<string,{start:number;court:string}[]>();
    for(const match of matches){const options:{start:number;court:string}[]=[];
        for(const date of state.dates){const anchor=state.anchors[date];for(const court of state.courts)for(const window of court.windows){for(let start=Math.max(window.start,anchor);start+match.play+match.buffer<=window.end&&start<=anchor+570;start+=state.grid){if(localTime(start).slice(0,10)===date&&localTime(start).slice(11)>="08:00"){if(choiceCount++>50000||Date.now()>deadline)return searchLimit();options.push({start,court:court.id});}}}}
        options.sort((a,b)=>a.start-b.start||a.court.localeCompare(b.court));choices.set(match.id,options);
    }
    let nodes=0,attempts=0;const maxNodes=50000;
    let timedOut=false;
    function fits(match:Match,assignment:{start:number;court:string}){
        if(attempts++>maxNodes||Date.now()>deadline){timedOut=true;return false;}
        const candidate={...match,players:newCandidates.get(match.id)!,assignment},day=localTime(assignment.start).slice(0,10);
        for(const dependency of match.dependencies){const prior=work.get(dependency.id);if(!prior?.assignment||assignment.start<prior.assignment.start+prior.play+dependency.lag)return false;}
        for(const other of work.values()){
            if(!other.assignment)continue;
            if(other.assignment.court===assignment.court && assignment.start<other.assignment.start+other.play+other.buffer && other.assignment.start<assignment.start+match.play+match.buffer)return false;
            const shared=candidate.players.filter(id=>other.players.includes(id));if(!shared.length)continue;
            const earlier=assignment.start<other.assignment.start?candidate:other,later=earlier===candidate?other:candidate;
            if(later.assignment!.start-earlier.assignment!.start-earlier.play<minimumRestBetween(state,earlier,later,shared))return false;
        }
        for(const player of candidate.players){let count=1;for(const other of work.values())if(other.players.includes(player)&&other.assignment&&localTime(other.assignment.start).slice(0,10)===day)count++;if(count>state.daily_limit)return false;}
        return true;
    }
    function visit(index:number):boolean{
        if(index===matches.length)return true;
        if(nodes++>maxNodes||attempts>maxNodes||Date.now()>deadline){timedOut=true;return false;}
        const match=matches[index];for(const assignment of choices.get(match.id)!){if(attempts>maxNodes||Date.now()>deadline){timedOut=true;return false;}if(!fits(match,assignment))continue;match.assignment=assignment;work.set(match.id,{...match,players:newCandidates.get(match.id)!});if(visit(index+1))return true;work.delete(match.id);match.assignment=null;}
        return false;
    }
    if(!visit(0))return [{key:"encouragement-capacity",code:timedOut||attempts>maxNodes?"SEARCH_LIMIT":"NO_CAPACITY",matches:matches.map(match=>match.id),message:timedOut||attempts>maxNodes?"鼓勵賽排程搜尋已達上限，尚未找到可驗證安排；請先在地端調整可用時段。":"保留既有場次與休息限制後沒有可用的鼓勵賽安排，請先調整未開賽賽程或場地時段。"}];
    return [];
}
export function reviewEncouragement(current:Tournament,divisionId:string,seed:number,generatedAt=new Date().toISOString()){
    const state=readTournament(structuredClone(current)),source=encouragementSources(state).find(source=>source.division_id===divisionId);
    if(!source)throw new Error("這個組別沒有可核對的直接淘汰首輪來源，請先核對地端交接。");
    const q=qualification(state,source);
    if(!["ready","insufficient"].includes(q.status))throw new Error(q.status==="drawn"?"此組已完成鼓勵賽抽籤，不能重複產生。":q.blockers[0]??"此組首輪成績尚未齊全，不能抽鼓勵賽。");
    if(!Number.isInteger(seed)||seed<0||seed>4294967295)throw new Error("抽籤種子無效。");
    const plan=bracketGeometry(state,source,q,seed,generatedAt),issues=scheduleNewMatches(state,source,q,plan.matches,plan.candidates);
    const entries=new Map((state.entries??[]).map(entry=>[entry.id,entry]));for(const eligible of q.eligible){const prior=entries.get(eligible.entry_id);if(prior&&prior.player_id!==eligible.player_id)throw new Error("鼓勵賽參賽身分與既有名單不一致。");entries.set(eligible.entry_id,{id:eligible.entry_id,player_id:eligible.player_id});}
    state.encouragement_sources ??= {source_revision:state.source_revision,divisions:encouragementSources(state)};
    state.entries=[...entries.values()];state.encouragement_draws=[...(state.encouragement_draws??[]),plan.draw];state.matches.push(...plan.matches);
    if(!issues.length)issues.push(...validate(state,new Set(plan.matches.map(match=>match.id))));
    const changes=plan.matches.map(match=>({id:match.id,code:match.code,division:match.division,side_a:match.side_a,side_b:match.side_b,start_at:match.assignment?new Date(match.assignment.start*60000).toISOString():null,court:state.courts.find(court=>court.id===match.assignment?.court)?.name??null}));
    return {state,division:source.division,qualification:q,draw:plan.draw,matches:plan.matches,issues,changes,can_apply:issues.length===0};
}
