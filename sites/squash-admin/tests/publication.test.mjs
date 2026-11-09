import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixture.mjs';
import {emptyLive,envelope,transitionLive} from '../outputs/publication-model.mjs';
const now='2030-01-01T00:00:00.000Z';
const setup=()=>{const main=fixture();return {main,pub:{schedule:structuredClone(main),live:emptyLive(main)}};};
test('live start and end protect canonical schedule and never create results',()=>{const {main,pub}=setup();const started=transitionLive(main,pub,{action:'start',court_id:'a',match_id:'m1'},now);assert.equal(main.matches[0].status,'planned');assert.equal(started.main.matches[0].status,'in_progress');assert.equal(started.publication.live.courts.a.match_id,'m1');const ended=transitionLive(started.main,started.publication,{action:'end',court_id:'a',match_id:'m1'},now);assert.equal(ended.main.matches[0].status,'completed');assert.equal(ended.publication.live.courts.a.match_id,null);assert.equal(ended.publication.live.starts.m1,now);});
test('cancel restores ready state and removes false actual start',()=>{const {main,pub}=setup();main.matches[0].status='ready';pub.schedule.matches[0].status='ready';const started=transitionLive(main,pub,{action:'start',court_id:'a',match_id:'m1'},now);const canceled=transitionLive(started.main,started.publication,{action:'cancel',court_id:'a',match_id:'m1'},now);assert.equal(canceled.main.matches[0].status,'ready');assert.equal(canceled.publication.live.starts.m1,undefined);});
test('reject unresolved, occupied courts, cross-court players and unpublished assignment',()=>{let {main,pub}=setup();main.matches[0].players=[];assert.throws(()=>transitionLive(main,pub,{action:'start',court_id:'a',match_id:'m1'},now));({main,pub}=setup());main.matches[0].assignment.start++;assert.throws(()=>transitionLive(main,pub,{action:'start',court_id:'a',match_id:'m1'},now));({main,pub}=setup());const started=transitionLive(main,pub,{action:'start',court_id:'a',match_id:'m1'},now);assert.throws(()=>transitionLive(started.main,started.publication,{action:'start',court_id:'a',match_id:'m1'},now));assert.throws(()=>transitionLive(started.main,started.publication,{action:'start',court_id:'b',match_id:'m2'},now));assert.throws(()=>transitionLive(started.main,started.publication,{action:'end',court_id:'a',match_id:'m2'},now));});
test('public projection excludes private IDs, rules, history and source metadata',()=>{const {pub}=setup();const result=envelope(pub,{source_id:'stream',sequence:1,schedule_revision:'revision',published_at:now,live_updated_at:null,created_at:now});assert.deepEqual(Object.keys(result.matches[0]).sort(),['id','code','division','side_a','side_b','status','court_id','planned_start_at','started_at'].sort());assert(!JSON.stringify(result).includes('minimum_rest'));assert(!Object.hasOwn(result.matches[0],'players'));});
test('starting a match on another court is rejected until the schedule is changed and published',()=>{
 const {main,pub}=setup(),before=structuredClone({main,pub});
 assert.throws(()=>transitionLive(main,pub,{action:'start',court_id:'b',match_id:'m1'},now),/其他場地/);
 assert.deepEqual({main,pub},before);
 main.matches[0].assignment.court='b';
 assert.throws(()=>transitionLive(main,pub,{action:'start',court_id:'b',match_id:'m1'},now),/未發布/);
 pub.schedule.matches[0].assignment.court='b';
 const started=transitionLive(main,pub,{action:'start',court_id:'b',match_id:'m1'},now);
 assert.equal(started.publication.live.courts.b.match_id,'m1');
});
test('finishing records the validated score in canonical and allowlisted public data',()=>{
 const {main,pub}=setup(),started=transitionLive(main,pub,{action:'start',court_id:'a',match_id:'m1'},now),result={rules:{best_of:3,target:11,margin:2},winner:'a',games:[{side_a:11,side_b:0},{side_a:12,side_b:10}]};
 const ended=transitionLive(started.main,started.publication,{action:'end',court_id:'a',match_id:'m1',result},now);
 assert.deepEqual(ended.main.matches[0].result,result);assert.deepEqual(ended.publication.schedule.matches[0].result,result);
 assert.deepEqual(envelope(ended.publication,{}).matches[0].result,result);assert.equal(ended.publication.live.courts.a.match_id,null);
});
test('first publication does not hide confirmed matches from court selection',async()=>{
 const {selectableMatches}=await import('../outputs/publication-model.mjs');
 const main=fixture(),before=structuredClone(main),queue=selectableMatches(main,null);
 assert.equal(queue.length,2);assert(queue.every(row=>row.eligible));assert(queue.every(row=>row.blocked_reason===null));assert.deepEqual(main,before);
 main.matches[0].players=[];
 assert.equal(selectableMatches(main,null).find(row=>row.match.id==='m1').blocked_reason,'雙方選手尚未確定');
 main.matches[1].status='completed';assert.equal(selectableMatches(main,null).some(row=>row.match.id==='m2'),false);
});
test('published queue explains blocked drafts and excludes unassigned matches',async()=>{
 const {selectableMatches}=await import('../outputs/publication-model.mjs');const {main,pub}=setup();
 main.matches[0].assignment.start+=15;
 const blocked=selectableMatches(main,pub).find(row=>row.match.id==='m1');assert.equal(blocked.eligible,false);assert.equal(blocked.blocked_reason,'請先發布這場的賽程調整');
 main.matches[1].assignment=null;assert.equal(selectableMatches(main,null).some(row=>row.match.id==='m2'),false);
});

test('each court queue contains only that court in the published schedule, including blocked drafts',async()=>{
 const {selectableMatches}=await import('../outputs/publication-model.mjs');const {main,pub}=setup();
 assert.deepEqual(selectableMatches(main,null,'a').map(row=>row.match.id),['m1']);
 assert.deepEqual(selectableMatches(main,pub,'b').map(row=>row.match.id),['m2']);
 assert.deepEqual(selectableMatches(main,pub,'unknown'),[]);
 main.matches[0].assignment.court='b';
 const a=selectableMatches(main,pub,'a');assert.equal(a[0].eligible,false);assert.match(a[0].blocked_reason,/發布/);
 assert.deepEqual(selectableMatches(main,pub,'b').map(row=>row.match.id),['m2']);
 pub.schedule.matches[0].assignment.court='b';
 assert.deepEqual(selectableMatches(main,pub,'a'),[]);assert.deepEqual(selectableMatches(main,pub,'b').map(row=>row.match.id),['m1','m2']);
 main.matches[0].status='completed';pub.schedule.matches[0].status='completed';
 assert.deepEqual(selectableMatches(main,pub,'b').map(row=>row.match.id),['m2']);
});
