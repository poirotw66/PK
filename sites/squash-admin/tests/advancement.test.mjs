import assert from 'node:assert/strict';
import test from 'node:test';
import { fixture } from './fixture.mjs';
import { reviewAdvancement, resolvedEntryIds, poolBasis } from '../outputs/advancement-model.mjs';
export function advancing() {
 const state=fixture();
 state.entries=[{id:'e1',player_id:'p1'},{id:'e2',player_id:'p2'}];
 Object.assign(state.matches[0],{slots:{a:{kind:'entry',entry_id:'e1'},b:{kind:'entry',entry_id:'e2'}},entry_ids:{a:'e1',b:'e2'},status:'completed',result:{rules:{best_of:3,target:11,margin:2},winner:'b',games:[{side_a:1,side_b:11},{side_a:2,side_b:11}]}});
 Object.assign(state.matches[1],{side_a:'第1場勝者',side_b:'第1場敗者',players:[],slots:{a:{kind:'winner',match_id:'m1'},b:{kind:'loser',match_id:'m1'}},entry_ids:{a:null,b:null},dependencies:[{id:'m1',lag:60}]});
 return state;
}
test('advancement previews actual winner and loser without silently changing published identity',()=>{
 const state=advancing();assert.deepEqual(resolvedEntryIds(state,state.matches[1]),{a:null,b:null});
 const result=reviewAdvancement(state);assert.equal(result.can_apply,true);assert.equal(result.changes.length,1);
 assert.deepEqual(result.state.matches[1].entry_ids,{a:'e2',b:'e1'});assert.equal(result.state.matches[1].side_a,state.players[1].name);assert.deepEqual(result.state.matches[1].players,['p1','p2']);
 assert.deepEqual(resolvedEntryIds(result.state,result.state.matches[1]),{a:'e2',b:'e1'});assert.deepEqual(state.matches[1].entry_ids,{a:null,b:null});
});
test('unconfirmed results, double forfeits and legacy labels never guess a winner',()=>{
 const state=advancing();state.matches[0].status='planned';delete state.matches[0].result;
 assert.equal(reviewAdvancement(state).changes.length,0);assert.equal(reviewAdvancement(state).pending.length,1);
 state.matches[0].status='completed';state.matches[0].result={rules:{best_of:3,target:11,margin:2},outcome:'double_forfeit',winner:null,games:[]};assert.equal(reviewAdvancement(state).pending.length,1);
 delete state.matches[1].slots;assert.equal(reviewAdvancement(state).changes.length,0);
});
test('already played or locked downstream matches and newly conflicting schedules block advancement',()=>{
 for(const mutate of [m=>m.status='in_progress',m=>m.lock_time=true,m=>m.lock_court=true,m=>m.assignment.start-=30]){const state=advancing();mutate(state.matches[1]);assert.equal(reviewAdvancement(state).can_apply,false);}
});
test('pool ranks require complete results and explicit full rankings; changed results invalidate the decision',()=>{
 const state=advancing();state.advancement_pools=[{id:'pool',stage_id:'stage',label:'A池',entry_ids:['e1','e2'],match_ids:['m1']}];state.matches[1].slots={a:{kind:'pool_rank',pool_id:'pool',rank:1},b:{kind:'pool_rank',pool_id:'pool',rank:2}};
 assert.equal(reviewAdvancement(state).can_apply,false);assert.throws(()=>reviewAdvancement(state,[{stage_id:'stage',pool_id:'pool',entry_ids:['e1','e1']}]),/每位選手一次/);
 const reviewed=reviewAdvancement(state,[{stage_id:'stage',pool_id:'pool',entry_ids:['e2','e1']}]);assert.equal(reviewed.can_apply,true);assert.equal(reviewed.state.advancement_decisions[0].basis,poolBasis(state,'pool'));
 reviewed.state.matches[0].result.games[0].side_a=3;assert.equal(reviewAdvancement(reviewed.state).pending.length,1);
});
test('unknown and cyclic advancement source references are rejected',()=>{
 const state=advancing();state.matches[0].slots.a={kind:'winner',match_id:'m2'};assert.throws(()=>reviewAdvancement(state),/循環/);
 state.matches[0].slots.a={kind:'winner',match_id:'missing'};assert.throws(()=>reviewAdvancement(state),/不存在/);
});

test('legacy metadata upgrade preserves results and assignments and rejects another lineage',()=>{
 const source=advancing(),legacy=structuredClone(source);
 delete legacy.entries; for(const match of legacy.matches){delete match.slots;delete match.entry_ids;}
 const reviewed=reviewAdvancement(legacy,[],source);assert.equal(reviewed.source_attached,true);assert.equal(reviewed.can_apply,true);assert.deepEqual(reviewed.state.matches[0].result,legacy.matches[0].result);assert.deepEqual(reviewed.state.matches[1].assignment,legacy.matches[1].assignment);
 const foreign=structuredClone(source);foreign.source_revision='other';assert.throws(()=>reviewAdvancement(legacy,[],foreign),/來源版本/);
 const mismatch=structuredClone(source);mismatch.players[0].name='other';assert.throws(()=>reviewAdvancement(legacy,[],mismatch));
});
