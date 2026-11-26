import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {encouragementStatus,reviewEncouragement} from '../outputs/encouragement-model.mjs';
import {readTournament} from '../outputs/schema.mjs';
import {encouragementFixture} from './encouragement-fixture.mjs';

test('divisions qualify independently; pending results never produce a draw',()=>{
 const s=encouragementFixture(),before=structuredClone(s),q=encouragementStatus(s);
 assert.deepEqual(q.divisions.map(d=>[d.status,d.completed,d.total]),[['ready',2,2],['waiting',0,2]]);
 assert.throws(()=>reviewEncouragement(s,'d1',8),/尚未齊全/);assert.deepEqual(s,before);
});
test('played first-round losers including seeds qualify, walkovers do not; retirement blocks',()=>{
 const s=encouragementFixture();s.matches[0].result={rules:{best_of:3,target:11,margin:2},winner:'a',outcome:'walkover',games:[]};
 let q=encouragementStatus(s).divisions[0];assert.equal(q.status,'insufficient');assert.deepEqual(q.eligible.map(e=>e.entry_id),['e4']);assert.equal(q.excluded.length,1);
 s.matches[0].result={rules:{best_of:3,target:11,margin:2},winner:'a',outcome:'retired',games:[]};q=encouragementStatus(s).divisions[0];assert.equal(q.status,'blocked');assert.match(q.blockers[0],/退賽/);
});
test('review generates n-1 best-of-three matches, schedules rest, and preserves all originals',()=>{
 const s=encouragementFixture(),original=structuredClone(s.matches),plan=reviewEncouragement(s,'d0',20261126,'2026-11-26T06:00:00.000Z');
 assert.equal(plan.can_apply,true,JSON.stringify(plan.issues));assert.equal(plan.matches.length,1);assert.equal(plan.matches[0].scoring_rules.best_of,3);assert.ok(plan.matches[0].assignment.start>=s.matches[0].assignment.start+70);
 assert.deepEqual(plan.state.matches.slice(0,original.length),original);assert.deepEqual(s.matches,original);assert.deepEqual(readTournament(plan.state),plan.state);
 assert.equal(encouragementStatus(plan.state).divisions[0].status,'drawn');assert.throws(()=>reviewEncouragement(plan.state,'d0',2),/重複/);
 const again=reviewEncouragement(s,'d0',20261126,'2026-11-26T06:00:00.000Z');assert.deepEqual(again.draw,plan.draw);assert.deepEqual(again.matches,plan.matches);
});
test('no-competition record has no fake games and still prevents duplicate draws',()=>{
 const s=encouragementFixture();for(const m of s.matches.slice(0,2))m.result={rules:{best_of:3,target:11,margin:2},winner:'a',outcome:'walkover',games:[]};
 const plan=reviewEncouragement(s,'d0',1);assert.equal(plan.can_apply,true);assert.deepEqual(plan.draw.entry_ids,[]);assert.deepEqual(plan.draw.bracket_entry_ids,[]);assert.deepEqual(plan.matches,[]);assert.deepEqual(readTournament(plan.state),plan.state);
});
test('backup/import rejects changed qualification, forged bracket, extra match or scoring',()=>{
 const plan=reviewEncouragement(encouragementFixture(),'d0',9);
 for(const mutate of [s=>{s.matches[0].result.winner='b';s.matches[0].result.games=s.matches[0].result.games.map(g=>({side_a:g.side_b,side_b:g.side_a}));},s=>s.encouragement_draws[0].entry_ids.push('e1'),s=>s.encouragement_draws[0].bracket_entry_ids.reverse(),s=>s.matches.at(-1).scoring_rules.best_of=5,s=>s.encouragement_draws[0].source_match_ids.pop()]){
  const state=structuredClone(plan.state);mutate(state);assert.throws(()=>readTournament(state),/鼓勵/);
 }
});
test('no capacity cannot silently move completed games or claim valid assignment',()=>{
 const s=encouragementFixture();for(const c of s.courts)c.windows=[{start:s.matches[0].assignment.start,end:s.matches[0].assignment.start+15}];
 const plan=reviewEncouragement(s,'d0',1);assert.equal(plan.can_apply,false);assert.ok(plan.issues.some(i=>['NO_CAPACITY','SEARCH_LIMIT'].includes(i.code)));assert.deepEqual(plan.state.matches.slice(0,s.matches.length),s.matches);
});
test('bundled real source catalog identifies only the two direct knockout groups',async()=>{
 const catalog=JSON.parse(await readFile('lib/encouragement-catalog.json','utf8')),s=encouragementFixture();
 s.source_revision=catalog.source_revision;delete s.encouragement_sources;s.players=[];s.matches=[];
 for(const group of catalog.divisions)for(const source of group.first_round)for(const side of [source.side_a,source.side_b]){assert.equal('name' in side,false);side.name='SyntheticPlayer'+side.player_id;}
 for(const group of catalog.divisions)for(const source of group.first_round){for(const side of [source.side_a,source.side_b])s.players.push({id:side.player_id,name:side.name,rest:0});s.matches.push({...structuredClone(encouragementFixture().matches[0]),id:source.match_id,code:source.match_id,division:group.division,side_a:source.side_a.name,side_b:source.side_b.name,players:[source.side_a.player_id,source.side_b.player_id],status:'planned'});delete s.matches.at(-1).result;}
 const q=encouragementStatus(s).divisions;
 assert.deepEqual(q.map(row=>[row.division,row.completed,row.total,row.pending.length]),[['國中男子組',0,8,8],['國中女子組',0,5,5]]);
});

test('five qualified losers receive three distributed byes and only four games',()=>{
 const s=encouragementFixture(),template=s.matches[0],first=s.encouragement_sources.divisions[0],start=template.assignment.start;
 s.players=Array.from({length:10},(_,i)=>({id:'p'+(i+1),name:'Player '+(i+1),rest:0}));
 s.matches=Array.from({length:5},(_,i)=>({...structuredClone(template),id:'m'+i,code:String(i),side_a:'Player '+(2*i+1),side_b:'Player '+(2*i+2),players:['p'+(2*i+1),'p'+(2*i+2)],assignment:{start:start+Math.floor(i/2)*15,court:i%2?'b':'a'}}));
 first.first_round=s.matches.map(m=>({match_id:m.id,side_a:{entry_id:'e'+m.players[0],player_id:m.players[0],name:m.side_a},side_b:{entry_id:'e'+m.players[1],player_id:m.players[1],name:m.side_b}}));s.encouragement_sources.divisions=[first];
 const plan=reviewEncouragement(s,'d0',42);assert.equal(plan.can_apply,true,JSON.stringify(plan.issues));assert.equal(plan.matches.length,4);assert.equal(plan.draw.bracket_entry_ids.length,8);assert.equal(plan.draw.bracket_entry_ids.filter(e=>e===null).length,3);assert.equal(new Set(plan.draw.entry_ids).size,5);assert.ok(plan.matches.every(m=>m.scoring_rules.best_of===3&&!m.championship));assert.deepEqual(readTournament(plan.state),plan.state);
});
