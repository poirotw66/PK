import assert from 'node:assert/strict';
import test from 'node:test';
import { fixture } from './fixture.mjs';
import { reviewCoachRules } from '../outputs/coach-rules-model.mjs';
import { readTournament } from '../outputs/schema.mjs';
import { reviewResultChange } from '../outputs/result-review-model.mjs';
const three={best_of:3,target:11,margin:2},five={...three,best_of:5};
test('explicit championship selection sets future match rules and allows walkover without inferring names',()=>{
 const state=fixture();state.matches[0].stage='Final';state.matches[1].scoring_rules={...five,target:15};
 const reviewed=reviewCoachRules(state,['m2']);
 assert.equal(state.coach_profile,undefined);assert.equal(state.matches[1].scoring_rules.best_of,5);
 assert.deepEqual(reviewed.state.coach_profile,{version:1,championship_ids:['m2']});
 assert.equal(reviewed.state.matches[0].championship,false);assert.equal(reviewed.state.matches[0].scoring_rules.best_of,3);
 assert.equal(reviewed.state.matches[1].championship,true);assert.equal(reviewed.state.matches[1].scoring_rules.target,15);
 assert.ok(reviewed.state.matches.every(match=>match.allowed_outcomes.includes('walkover')));
 const result=reviewResultChange(reviewed.state,'m1',{rules:three,outcome:'walkover',winner:'b',games:[]});assert.equal(result.state.matches[0].result.winner,'b');
 assert.throws(()=>reviewResultChange(state,'m1',{rules:three,outcome:'walkover',winner:'b',games:[]}),/規則/);
});
test('selection can turn a future five-game nonfinal into three and final into five with preview changes',()=>{
 const state=fixture();state.matches[0].scoring_rules=five;
 const reviewed=reviewCoachRules(state,['m2']);
 assert.deepEqual(reviewed.changes,[{id:'m1',code:'1',before:5,after:3},{id:'m2',code:'2',before:3,after:5}]);
});
test('completed and already started scoring rules and results stay intact while championship metadata changes',()=>{
 const state=fixture();Object.assign(state.matches[0],{status:'completed',scoring_rules:five,result:{rules:five,winner:'a',games:[{side_a:11,side_b:0},{side_a:11,side_b:1},{side_a:11,side_b:2}]}});Object.assign(state.matches[1],{status:'in_progress',scoring_rules:three});
 const reviewed=reviewCoachRules(state,['m2']);assert.deepEqual(reviewed.changes,[]);
 assert.deepEqual(reviewed.state.matches[0].result,state.matches[0].result);assert.deepEqual(reviewed.state.matches[0].scoring_rules,five);assert.deepEqual(reviewed.state.matches[1].scoring_rules,three);
});
test('profile validates known unique IDs and only schema version 1',()=>{
 assert.throws(()=>reviewCoachRules(fixture(),['missing']),/不存在/);assert.throws(()=>reviewCoachRules(fixture(),['m1','m1']),/重複/);
 const state=fixture();state.coach_profile={version:1,championship_ids:['missing']};assert.throws(()=>readTournament(state),/不存在/);
 state.coach_profile={version:1,championship_ids:['m1','m1']};assert.throws(()=>readTournament(state));state.coach_profile={version:2,championship_ids:[]};assert.throws(()=>readTournament(state));
});
test('known structural final markers cannot be overwritten by selection and stay enforced after first legacy review',()=>{
 const state=fixture();state.matches[0].championship=true;state.matches[1].championship=false;
 assert.throws(()=>reviewCoachRules(state,[]),/已核定籤表/);assert.throws(()=>reviewCoachRules(state,['m1','m2']),/已核定籤表/);
 assert.equal(reviewCoachRules(state,['m1']).state.matches[0].scoring_rules.best_of,5);
 const legacy=reviewCoachRules(fixture(),['m2']).state;assert.throws(()=>reviewCoachRules(legacy,['m1']),/已核定籤表/);
});
