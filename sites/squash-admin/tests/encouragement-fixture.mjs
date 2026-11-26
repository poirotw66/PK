import {fixture} from './fixture.mjs';
export function encouragementFixture(){
 const s=fixture(),start=s.matches[0].assignment.start;
 s.players=[1,2,3,4,5,6,7,8].map(i=>({id:'p'+i,name:'Player '+i,rest:0}));
 s.matches=[1,2,3,4].map((i)=>({...structuredClone(s.matches[0]),id:'m'+i,code:String(i),division:i<=2?'Group A':'Group B',stage:'淘汰賽',group:null,side_a:'Player '+(i*2-1),side_b:'Player '+i*2,players:['p'+(i*2-1),'p'+i*2],assignment:{start:start+(i<=2?0:150),court:i%2?'a':'b'},status:i<=2?'completed':'planned',...(i<=2?{result:{rules:{best_of:3,target:11,margin:2},winner:'a',outcome:'played',games:[{side_a:11,side_b:5},{side_a:11,side_b:6}]}}:{})}));
 s.encouragement_sources={source_revision:s.source_revision,divisions:[['Group A',['m1','m2']],['Group B',['m3','m4']]].map(([division,ids],i)=>({division_id:'d'+i,division,stage_id:'stage'+i,first_round:ids.map(id=>{const m=s.matches.find(m=>m.id===id);return {match_id:id,side_a:{entry_id:'e'+m.players[0].slice(1),player_id:m.players[0],name:m.side_a},side_b:{entry_id:'e'+m.players[1].slice(1),player_id:m.players[1],name:m.side_b}};})}))};
 return s;
}
