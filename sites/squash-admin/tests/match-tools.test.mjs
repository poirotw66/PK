import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixture.mjs';
import {reviewSwap,playerTimelines} from '../outputs/match-tools.mjs';
import {withDraft,mergeDraft} from '../outputs/placements.mjs';
import {validate} from '../outputs/rules.mjs';

test('swap is pure, simultaneous and preserves each match duration',()=>{
 const s=fixture(),before=structuredClone(s),plan=reviewSwap(s,'m1','m2');
 assert.equal(plan.can_apply,true);assert.equal(plan.changes.length,2);assert.deepEqual(s,before);
 const after=withDraft(s,mergeDraft(s,{},plan.updates));
 assert.deepEqual(after.matches[0].assignment,s.matches[1].assignment);
 assert.deepEqual(after.matches[1].assignment,s.matches[0].assignment);
 assert.deepEqual(validate(after),[]);assert.equal(after.matches[0].play,s.matches[0].play);
});
test('swap rejects identical, missing, unscheduled, started and locked matches',()=>{
 const s=fixture();assert.equal(reviewSwap(s,'m1','m1').can_apply,false);assert.equal(reviewSwap(s,'m1','missing').can_apply,false);
 s.matches[1].assignment=null;assert.equal(reviewSwap(s,'m1','m2').can_apply,false);
 s.matches[1]=fixture().matches[1];s.matches[1].status='in_progress';assert.equal(reviewSwap(s,'m1','m2').can_apply,false);
 s.matches[1].status='planned';s.matches[1].lock_time=true;assert.equal(reviewSwap(s,'m1','m2').can_apply,false);
});
test('longer match cannot fit in another match slot without checking following court use',()=>{
 const s=fixture(),start=s.matches[0].assignment.start;s.matches[0].players=[];s.matches[0].play=30;s.matches[1].players=[];
 s.matches.push({...structuredClone(s.matches[1]),id:'m3',code:'3',group:null,assignment:{start:start+90,court:'b'}});
 const plan=reviewSwap(s,'m1','m2');assert.equal(plan.can_apply,false);assert.ok(plan.issues.some(i=>i.code==='COURT_OVERLAP'));
});
test('swap rechecks dependencies and the full schedule',()=>{
 const s=fixture();s.matches[1].dependencies=[{id:'m1',lag:0}];
 const plan=reviewSwap(s,'m1','m2');assert.equal(plan.can_apply,false);assert.ok(plan.issues.some(i=>i.code==='DEPENDENCY'));
});
test('swapping between dates cannot split a round-robin group',()=>{
 const s=fixture();s.matches.push({...structuredClone(s.matches[0]),id:'m3',code:'3',group:null,players:[],assignment:{start:s.anchors[s.dates[1]],court:'a'}});
 const plan=reviewSwap(s,'m1','m3');assert.equal(plan.can_apply,false);assert.ok(plan.issues.some(i=>i.code==='GROUP_SPLIT'));
});
test('exchange includes existing draft positions and retains unrelated edits',()=>{
 const s=fixture(),start=s.matches[0].assignment.start;
 s.matches.push({...structuredClone(s.matches[0]),id:'m3',code:'3',group:null,players:[],assignment:{start:start+240,court:'a'}});
 const draft={m1:{id:'m1',start:start+15,court:'a'},m2:{id:'m2',start:start+90,court:'b'},m3:{id:'m3',start:start+255,court:'b'}};
 const plan=reviewSwap(withDraft(s,draft),'m1','m2');assert.equal(plan.can_apply,true);
 const merged=mergeDraft(s,draft,plan.updates);assert.deepEqual(merged.m3,draft.m3);assert.equal(merged.m2.start,draft.m1.start);assert.equal(merged.m1.start,draft.m2.start);
 const unsafe={...draft,m2:{...draft.m2,start:start+75}};assert.equal(reviewSwap(withDraft(s,unsafe),'m1','m2').can_apply,false);
});
test('player timeline uses play end, includes cross-division matches and court transfer',()=>{
 const s=fixture();s.matches[1].division='Another synthetic division';s.matches[0].buffer=25;s.matches[0].transfer=5;
 s.players[0].rest=70;
 const rows=playerTimelines(s,'m1');assert.equal(rows.length,2);assert.equal(rows[0].previous,null);
 assert.equal(rows[0].next.match.id,'m2');assert.equal(rows[0].next.gap,65);assert.equal(rows[0].next.required,75);assert.equal(rows[0].next.sufficient,false);
 assert.equal(rows[1].next.required,65);assert.equal(rows[1].next.sufficient,true);
 const later=playerTimelines(s,'m2');assert.equal(later[0].previous.match.id,'m1');assert.equal(later[0].next,null);
});
test('timeline derives from draft, uses confirmed IDs and handles unknown opponents',()=>{
 const s=fixture(),start=s.matches[0].assignment.start;
 s.players.push({id:'other',name:s.players[0].name,rest:0});
 s.matches.push({...structuredClone(s.matches[1]),id:'m3',code:'3',players:['other'],assignment:{start:start+30,court:'a'}});
 const rows=playerTimelines(withDraft(s,{m2:{id:'m2',start:start+120,court:'b'}}),'m1');
 assert.equal(rows[0].next.match.id,'m2');assert.equal(rows[0].next.gap,110);
 s.matches[0].players=[];assert.deepEqual(playerTimelines(s,'m1'),[]);
 assert.deepEqual(playerTimelines(s,'missing'),[]);
});
test('simultaneous appearances show overlapping rest; unassigned source cannot fabricate intervals',()=>{
 const s=fixture();s.matches[1].assignment.start=s.matches[0].assignment.start;
 const all=[...playerTimelines(s,'m1'),...playerTimelines(s,'m2')];assert.ok(all.some(row=>row.next?.gap<0||row.previous?.gap<0));
 s.matches[0].assignment=null;assert.ok(playerTimelines(s,'m1').every(row=>row.previous===null&&row.next===null));
});


test('time locks allow a court-only exchange at the same start',()=>{
 const s=fixture();s.matches[1].assignment.start=s.matches[0].assignment.start;
 for(const match of s.matches){match.players=[];match.lock_time=true;}
 assert.equal(reviewSwap(s,'m1','m2').can_apply,true);
});
test('cross-day exchange rechecks the daily workload cap',()=>{
 const s=fixture(),day2=s.anchors[s.dates[1]];
 s.matches[0].group=null;s.matches[0].players=['p1'];s.matches[1].group=null;s.matches[1].players=['p2'];s.matches[1].assignment={start:day2,court:'b'};
 for(let i=0;i<5;i++)s.matches.push({...structuredClone(s.matches[0]),id:'extra'+i,code:'X'+i,assignment:{start:day2+75*(i+1),court:'a'}});
 assert.deepEqual(validate(s),[]);
 const plan=reviewSwap(s,'m1','m2');assert.equal(plan.can_apply,false);assert.ok(plan.issues.some(issue=>issue.code==='DAILY_LIMIT'));
});
