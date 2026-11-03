import test from 'node:test';
import assert from 'node:assert/strict';
import {validate,review,parseLocal,localTime} from '../outputs/rules.mjs';
import {readTournament} from '../outputs/schema.mjs';

import {fixture} from './fixture.mjs';
const start=parseLocal('2030-01-01T08:00');
function codes(s){return validate(s).map(i=>i.code);}
test('valid schedule and exact start deadline',()=>{
 const s=readTournament(fixture());assert.deepEqual(validate(s),[]);
 s.matches[0].assignment.start=parseLocal('2030-01-01T17:30');s.matches.pop();assert.deepEqual(validate(s),[]);
 s.matches[0].assignment.start++;assert.ok(codes(s).includes('START_WINDOW'));
 s.matches[0].assignment.start=parseLocal('2030-01-01T07:45');assert.ok(codes(s).includes('START_WINDOW'));
});
test('rest uses play end and adds transfer time',()=>{
 const s=fixture();s.matches[1].assignment.start=start+60;assert.ok(codes(s).includes('PLAYER_REST'));
 s.matches[1].assignment.start=start+75;assert.ok(!codes(s).includes('PLAYER_REST'));
 s.matches[1].transfer=10;assert.ok(codes(s).includes('PLAYER_REST'));
});
test('court buffers and blackouts cannot be bypassed',()=>{
 const s=fixture();s.matches[1].players=[];s.matches[1].assignment={start:start+10,court:'a'};
 assert.ok(codes(s).includes('COURT_OVERLAP'));
 s.matches[1].assignment.start=start+15;assert.ok(!codes(s).includes('COURT_OVERLAP'));
 s.courts[0].windows=[{start,end:start+5}];assert.ok(codes(s).includes('COURT_WINDOW'));
});
test('confirmed players count across divisions; unresolved slots do not reserve players',()=>{
 const s=fixture();s.matches=Array.from({length:6},(_,i)=>({...structuredClone(s.matches[0]),id:'m'+i,division:'Division '+i,assignment:{start:start+i*75,court:'a'}}));
 assert.ok(codes(s).includes('DAILY_LIMIT'));
 s.matches.at(-1).players=[];assert.ok(!codes(s).includes('DAILY_LIMIT'));
});
test('dependencies, group dates and grid are enforced',()=>{
 const s=fixture();s.matches[1].dependencies=[{id:'m1',lag:90}];assert.ok(codes(s).includes('DEPENDENCY'));
 s.matches[1].assignment.start=start+1440;assert.ok(codes(s).includes('GROUP_SPLIT'));
 s.matches[1].assignment.start=start+76;assert.ok(codes(s).includes('GRID'));
});
test('review is pure and rejects unknown, duplicate, locked, started and unchanged updates',()=>{
 const s=fixture(),copy=structuredClone(s),update={id:'m1',start:start+1440,court:'a'};
 const plan=review(s,[update]);assert.equal(plan.can_apply,false);assert.deepEqual(s,copy);
 assert.throws(()=>review(s,[update,update]));
 assert.throws(()=>review(s,[{...update,id:'missing'}]));
 assert.throws(()=>review(s,[{...update,start}]));
 s.matches[0].lock_time=true;assert.throws(()=>review(s,[update]));
 s.matches[0].lock_time=false;s.matches[0].status='in_progress';assert.throws(()=>review(s,[update]));
});
test('valid batch review and invalid dates',()=>{
 const s=fixture();const plan=review(s,[{id:'m1',start:start+1440,court:'a'},{id:'m2',start:start+1515,court:'b'}]);
 assert.equal(plan.can_apply,true);assert.equal(plan.changes.length,2);
 assert.throws(()=>parseLocal('2030-02-30T08:00'));assert.equal(localTime(start),'2030-01-01T08:00');
});
test('imports reject unknown fields and missing references',()=>{
 const s=fixture();s.secret='not-allowed';assert.throws(()=>readTournament(s));delete s.secret;
 s.matches[0].players=['missing'];assert.throws(()=>readTournament(s));
});
