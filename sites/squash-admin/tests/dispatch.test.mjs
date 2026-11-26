import test from 'node:test';
import assert from 'node:assert/strict';
import {dispatchQueues,dispatchHeadWarnings,planDispatch} from '../outputs/dispatch-model.mjs';
import {fixture} from './fixture.mjs';
test('private queues start empty despite reference assignments and do not change them',()=>{
 const s=fixture(),before=structuredClone(s);assert.deepEqual(dispatchQueues(s),{a:[],b:[]});
 assert.deepEqual(planDispatch(s,undefined,'m1','a'),{a:['m1'],b:[]});assert.deepEqual(s,before);
});
test('queue supports append, reorder, remove, move and removes completed',()=>{
 const s=fixture();s.matches.push(...[3,4].map(i=>({...structuredClone(s.matches[0]),id:'m'+i})));
 let q=planDispatch(s,{},'m1','a');q=planDispatch(s,q,'m2','a');q=planDispatch(s,q,'m3','a');
 assert.throws(()=>planDispatch(s,q,'m4','a'),/三場/);
 q=planDispatch(s,q,'m3','a',0);assert.deepEqual(q.a,['m3','m1','m2']);
 q=planDispatch(s,q,'m3',null);assert.deepEqual(q.a,['m1','m2']);
 // Different actual players can occupy the next court head.
 s.matches[1].players=[];q=planDispatch(s,q,'m2','b');assert.deepEqual(q,{a:['m1'],b:['m2']});
 s.matches[0].status='completed';assert.deepEqual(dispatchQueues(s,q),{a:[],b:['m2']});
});
test('same actual player cannot lead both courts and completed cannot be queued',()=>{
 const s=fixture();const q=planDispatch(s,{},'m1','a');assert.throws(()=>planDispatch(s,q,'m2','b'),/同一位選手/);
 s.matches[1].status='completed';assert.throws(()=>planDispatch(s,q,'m2','a'),/已登錄/);
});

test('completion promotes the next queue item and warns of a shared player without changing scores or order',()=>{
 const s=fixture();
 const [first,next]=s.matches;
 first.players=['p1','p2'];next.players=['p3','p4'];
 s.matches.push({...structuredClone(next),id:'m3',players:['p3','p5']});
 const stored={a:[first.id,next.id],b:['m3']};
 assert.deepEqual(dispatchHeadWarnings(s,dispatchQueues(s,stored)),{a:null,b:null});
 first.status='completed';first.result={rules:{best_of:3,target:11,margin:2},outcome:'played',winner:'a',games:[{side_a:11,side_b:2},{side_a:11,side_b:3}]};
 const before=structuredClone(s),queues=dispatchQueues(s,stored),warnings=dispatchHeadWarnings(s,queues);
 assert.deepEqual(queues,{a:[next.id],b:['m3']});
 assert.match(warnings.a,/選手也列在/);assert.match(warnings.b,/選手也列在/);
 assert.deepEqual(s,before);assert.deepEqual(stored,{a:[first.id,next.id],b:['m3']});
});
test('unresolved court heads warn without blocking later score entry or deleting queues',()=>{
 const s=fixture();s.matches[0].players=[];const queues={a:['m1'],b:[]};
 assert.match(dispatchHeadWarnings(s,queues).a,/雙方選手尚未確認/);
 assert.equal(dispatchHeadWarnings(s,queues).b,null);assert.deepEqual(queues,{a:['m1'],b:[]});
});
